"""问题件页读取器（阶段一只读）：连接已登录的调试 Chrome（CDP 9222），
读取 problemQuery 页「需要我回复的」列表。

只读：本模块绝不提交任何表单/点击任何写按钮。
"""
import asyncio
import json
import random
import re
import uuid
from datetime import datetime

import httpx
from playwright.async_api import async_playwright

from shared import audit
from shared.config import CDP_WS_URL, PROBLEM_QUERY_URL, WS_HOST
from shared.models import ParcelList, ProblemParcel
from service.browser import BrowserSession


def _make_problem_id(row: dict) -> str:
    """问题事件锚：运单号+类型+登记时间 合成（§15.4，无天然键时确定性合成）。"""
    raw = f"{row.get('waybill_no','')}|{row.get('ptype','')}|{row.get('created_at','')}"
    return uuid.uuid5(uuid.NAMESPACE_URL, raw).hex[:16]


async def fetch_problem_list() -> ParcelList:
    """拉「需要我回复的」列表（阶段一只读）。

    策略：直接开 problemQuery 鉴权 URL（j_security_check 会话已在 Cookie 里），
    等表格渲染，然后 eval 读取表格行。
    """
    session = BrowserSession()
    try:
        p = await session.page()
        # 若已有 problemQuery tab 就直接读（别重新 goto——刷新会丢掉已查询的数据）
        if "problemQuery" not in p.url:
            p = await session.goto(PROBLEM_QUERY_URL)
        await p.wait_for_selector("table", timeout=30_000)
        await asyncio.sleep(3)
        rows = await p.evaluate(
            """
            () => {
              // 关键：表头（thead th）与数据行（tbody tr）在分开的 table 里（冻结列表格库）。
              // 找含『问题件类型』表头的 table，取其后第一个有数据行的兄弟 table 配对。
              const tables = Array.from(document.querySelectorAll('table'));
              for (let i = 0; i < tables.length; i++) {
                const ths = Array.from(tables[i].querySelectorAll('thead th')).map(th => th.innerText.trim());
                if (!ths.some(h => h.includes('问题件类型'))) continue;
                const idx = {};
                ths.forEach((h, j) => { if (h && !(h in idx)) idx[h] = j; });
                for (let k = i + 1; k < tables.length; k++) {
                  const dataRows = [];
                  for (const tr of tables[k].querySelectorAll('tbody tr')) {
                    let cells = Array.from(tr.querySelectorAll('td')).map(td => td.innerText.trim());
                    // 行首可能多出复选框/操作空列（表头也是空 th）；
                    // 若 cells 数与 th 数不一致，按「td 序与 th 序错位」对齐：
                    // 找第一个非空 td 的位置 j，把它对到第一个非空 th 的位置 h，
                    // 前面补 (h - j) 个空串使 cells[j..] 与 th[h..] 对齐。
                    if (cells.length !== ths.length) {
                      const hNonEmpty = ths.findIndex(h => h);
                      const jNonEmpty = cells.findIndex(c => c);
                      if (hNonEmpty >= 0 && jNonEmpty >= 0) {
                        const pad = hNonEmpty - jNonEmpty;
                        if (pad > 0) cells = Array(pad).fill('').concat(cells);
                        else if (pad < 0) cells = cells.slice(-pad);
                      }
                    }
                    dataRows.push(cells);
                  }
                  if (dataRows.length > 0) return {header: idx, rows: dataRows};
                }
              }
              return {header: null, rows: []};
            }
            """
        )
        header_idx = rows.get("header") or {}
        raw_rows = rows.get("rows", [])

        def col(cells, name):
            i = header_idx.get(name)
            return cells[i] if i is not None and i < len(cells) else None

        # 去重（左右冻结列结构可能产生重复行）
        seen = set()
        items = []
        for cells in raw_rows:
            key = tuple(cells)
            if key in seen:
                continue
            seen.add(key)
            row = {
                "outlet_from": col(cells, "发布网点"),
                "outlet_notify": col(cells, "通知网点"),
                "category": col(cells, "问题件一级类型"),
                "ptype": col(cells, "问题件类型"),
                "description": col(cells, "问题件描述"),
                "latest_reply": col(cells, "最新回复内容"),
                "latest_reply_at": col(cells, "最新回复时间"),
                "order_source": col(cells, "订单来源"),
                "creator": col(cells, "创建人"),
                "created_at": col(cells, "创建时间"),
                "status": col(cells, "快件状态"),
                "province": col(cells, "派件省区"),
            }
            row["problem_id"] = _make_problem_id(row)
            from service.rules import is_sensitive
            row["needs_human"] = is_sensitive("", row.get("description", ""))
            items.append(ProblemParcel(**row))
        result = ParcelList(
            total=len(items), scanned_at=datetime.now(), items=items
        )
        audit.append(
            "scan_problem_list",
            {"total": len(items), "sensitive": sum(1 for i in items if i.needs_human)},
        )
        return result
    finally:
        await session.close()
