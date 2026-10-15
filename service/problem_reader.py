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


class SessionExpired(Exception):
    """中通业务会话失效（接口 412 / code:412）——页面不跳登录，静默返回空数据。"""
    pass


# 会话失效判定口径（精确到门户域 + 业务接口，避免误报/漏报）
# 背景：旧口径 `"problem" in url or "query" in url` + 401/403/412 过宽——
# 无关接口（如 /api/user/queryPoints 返回 403）与 WAF 反爬 412 都会被误判成会话失效，
# 3 次即触发 breaker 停掉自动回复。
PORTAL_HOST_HINT = "zto56.com"                                   # 只认中通门户域
SESSION_URL_HINTS = ("problem", "session", "login", "auth")      # 业务/会话接口关键词
EXPIRED_STATUS = (401, 412)                                      # 403 是通用禁止，剔出
EXPIRED_BODY_CODES = (412, 720)                                  # body 假成功码


def _iter_codes(obj):
    """递归收集 JSON 里所有 code 字段（容忍嵌套/键序）。"""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == "code" and isinstance(v, int):
                yield v
            else:
                yield from _iter_codes(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _iter_codes(v)


def _body_is_expired(body: str) -> bool:
    """body 是否会话失效：优先结构化解析 code，兜底子串（容忍空格/换行）。"""
    try:
        obj = json.loads(body)
        if any(c in EXPIRED_BODY_CODES for c in _iter_codes(obj)):
            return True
    except Exception:
        pass
    if "无效session" in body:
        return True
    compact = body.replace(" ", "").replace("\n", "").replace("\r", "")
    return '"code":412' in compact or '"code":720' in compact


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
        # 会话失效检测（待办①）：点查询前挂响应监听，中通业务接口 412/code:412 → SessionExpired。
        # 中通页面不跳登录、静默显示空列表——必须监听接口才能区分"会话失效"与"真无数据"。
        session_state = {"expired": False, "codes": [], "bodies": []}

        def _on_response(resp):
            try:
                url = (resp.url or "").lower()
                if PORTAL_HOST_HINT not in url:
                    return
                hit_hint = any(h in url for h in SESSION_URL_HINTS)
                if hit_hint and resp.status in EXPIRED_STATUS:
                    session_state["codes"].append((url[:80], resp.status))
                    session_state["expired"] = True
                    return
                if hit_hint and resp.status == 200:
                    # HTTP 200 + body code 412/720（"无效session"）的假成功也要查。
                    # 收「全部」候选（不能 last-wins——会话接口不一定最后返回）
                    session_state["bodies"].append(resp)
            except Exception:
                pass

        p.on("response", _on_response)
        # 点「查询」刷新数据（同时触发接口——监听它的响应码）
        try:
            await p.evaluate("""
              () => {
                const btn = Array.from(document.querySelectorAll('button'))
                  .find(b => b.innerText.trim() === '查询');
                if (btn) btn.click();
              }
            """)
            await asyncio.sleep(4)
        except Exception:
            pass
        # HTTP 200 + body code 412/720 的假成功检查（遍历全部候选；text() 失败要留痕，不静默吞）
        for resp in session_state["bodies"]:
            try:
                body = await resp.text()
            except Exception as e:  # noqa: BLE001
                audit.append("session_check_body_error",
                             {"error": str(e)[:100], "url": (resp.url or "")[:80]},
                             actor="reader")
                continue
            if _body_is_expired(body):
                raise SessionExpired("中通业务会话失效（code 412/720 无效session）——请重新登录")
        if session_state["expired"]:
            raise SessionExpired("中通业务会话失效（接口 401/412）——请重新登录")
        await p.wait_for_selector("table", timeout=30_000)
        await asyncio.sleep(2)
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
