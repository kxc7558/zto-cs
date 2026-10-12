"""回复执行器（写操作，非资金）：在真实浏览器里把回复提交回中通系统。

两阶段（§15 设计）：
- prepare：生成预览快照与幂等键（不提交）
- commit：凭一次性令牌在浏览器里提交 + 回读校验

安全硬闸（代码强制，不受开关影响）：
- 敏感类（仲裁/理赔/责任/费用）绝不自动提交
- 提交前回读校验（界面提示成功不算数）
- 全程审计哈希链留痕
"""
import asyncio
import random

from shared import audit
from service.browser import BrowserSession
from service import rules
from db import ledger
from shared.config import PROBLEM_QUERY_URL, WS_HOST


async def _find_row_and_reply(page, waybill_no: str, reply_text: str,
                              desc_hint: str = "") -> dict:
    """在 problemQuery 列表里找到目标行，点开回复，填入并提交。

    行锚定：优先运单号；无运单号则用问题件描述（每行唯一）。
    返回 {submitted: bool, readback: str, note: str}。
    注意：这是写操作，仅在 commit 流程中调用（调用方已过全部闸门）。
    """
    needle = waybill_no or desc_hint
    if not needle:
        return {"submitted": False, "readback": "", "note": "no anchor (no waybill/desc)"}
    rows = await page.evaluate(
        """
        (needle) => {
          const tables = Array.from(document.querySelectorAll('table'));
          let headerTable = null;
          for (let i = 0; i < tables.length; i++) {
            const ths = Array.from(tables[i].querySelectorAll('thead th')).map(th => th.innerText.trim());
            if (ths.some(h => h.includes('问题件类型'))) { headerTable = i; break; }
          }
          if (headerTable === null) return {found: false};
          for (let k = headerTable + 1; k < tables.length; k++) {
            const trs = tables[k].querySelectorAll('tbody tr');
            for (const tr of trs) {
              const cells = Array.from(tr.querySelectorAll('td')).map(td => td.innerText.trim());
              const full = cells.join('|');
              if (needle && full.includes(needle.slice(0, 20))) {
                return {found: true, rowText: full.slice(0, 100), hasBtn: !!tr.querySelector('button, a')};
              }
            }
          }
          return {found: false};
        }
        """,
        needle,
    )
    if not rows.get("found"):
        return {"submitted": False, "readback": "", "note": "row not found on page"}

    # 回复对话框流程：点回复 → 填 textarea → 提交 → 回读
    # 注：真实对话框 DOM 需在首次真实提交时摸清（§15.8 待核项），
    # 此处按通用弹窗模式实现，并在回读校验失败时拒绝落「已提交」。
    dialog_btn = page.locator("tr", has_text=waybill_no).first.locator(
        "button:has-text('回复'), a:has-text('回复')"
    ).first
    try:
        await asyncio.sleep(random.uniform(2, 5))  # 反风控：拟人延迟
        await dialog_btn.click(timeout=10_000)
        await page.wait_for_selector("textarea, .el-textarea", timeout=10_000)
        ta = page.locator("textarea").first
        await ta.fill(reply_text)
        # 找提交按钮
        submit = page.locator(
            "button:has-text('确定'), button:has-text('提交'), button:has-text('回复')"
        ).last
        await submit.click(timeout=10_000)
        await asyncio.sleep(3)
        # 回读校验：最新回复内容列应出现刚提交的文本
        readback = await page.evaluate(
            """
            (waybill) => {
              const tables = Array.from(document.querySelectorAll('table'));
              for (const t of tables) {
                for (const tr of t.querySelectorAll('tbody tr')) {
                  const full = Array.from(tr.querySelectorAll('td')).map(td => td.innerText.trim()).join('|');
                  if (waybill && full.includes(waybill)) return full.slice(0, 300);
                }
              }
              return '';
            }
            """,
            waybill_no,
        )
        ok = reply_text[:12] in (readback or "")
        return {"submitted": ok, "readback": readback, "note": "ok" if ok else "readback-mismatch"}
    except Exception as e:
        return {"submitted": False, "readback": "", "note": f"error:{type(e).__name__}"}


async def commit_reply(problem_id: str, waybill_no: str | None, reply_text: str,
                       confirmed_by: str, auto: bool, desc_hint: str = "") -> dict:
    """两阶段的第二步：真实提交 + 回读校验 + 落账本与审计。

    调用方必须已完成：
    - prepare（生成幂等键）
    - 敏感类闸门检查（敏感类绝不走 auto）
    - 授权检查（write.enabled / 授权书记录）
    """
    # 硬闸再查一次（防御纵深）
    if auto:
        ptype_ok = not rules.is_sensitive("", reply_text)
        if not ptype_ok:
            return {"ok": False, "note": "sensitive content in reply — auto blocked"}

    key = ledger.make_idempotency_key(problem_id, "reply", reply_text, confirmed_by)
    existing = ledger.get_status(key)
    if existing and existing["status"] == "committed":
        return {"ok": True, "note": "already committed (idempotent)", "ledger": existing}
    if existing and existing["status"] == "pending":
        # 意图已落、可能上次没提交完——继续提交（幂等键相同）
        pass
    else:
        ledger.record_intent(problem_id, waybill_no, "reply", reply_text, key, auto, confirmed_by)

    session = BrowserSession()
    try:
        page = await session.page()
        if "problemQuery" not in page.url:
            await session.goto(PROBLEM_QUERY_URL)
        result = await _find_row_and_reply(page, waybill_no or "", reply_text,
                                           desc_hint=desc_hint)
        if result["submitted"]:
            ledger.record_result(key, "committed", platform_receipt=result["readback"][:200])
            audit.append("reply_committed", {
                "problem_id": problem_id, "waybill_no": waybill_no,
                "auto": auto, "confirmed_by": confirmed_by,
                "text_len": len(reply_text), "readback": result["readback"][:120],
            }, actor="auto" if auto else confirmed_by)
            return {"ok": True, "note": "committed", "readback": result["readback"]}
        else:
            ledger.record_result(key, "failed", platform_receipt=result.get("note"))
            audit.append("reply_failed", {
                "problem_id": problem_id, "auto": auto, "note": result.get("note"),
            }, actor="auto" if auto else confirmed_by)
            return {"ok": False, "note": result.get("note"), "readback": result.get("readback")}
    finally:
        await session.close()
