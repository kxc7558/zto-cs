"""插件桥接层：插件=手（页面里的读写），服务端=脑（决策/审核/留痕）。

插件上报的行格式与 problem_reader 的解析对齐（表头文本 -> 值的 dict）。
闸门逻辑全复用：开关 / 敏感硬闸 / AI 审核 / 幂等账本。
"""
from datetime import datetime

import asyncio

from shared import audit
from shared.models import ProblemParcel
from service import rules
from service.problem_reader import _make_problem_id
from db import ledger


def pending_confirmations() -> list[dict]:
    """待确认队列：已填好等人工确认的回复（工作台展示 + 确认下发）。
    confirmed_by 以 cdp:/extension: 开头的都算（摒弃插件后只有 cdp）。"""
    with _conn() as c:
        rows = c.execute(
            "SELECT * FROM write_ledger WHERE status='pending' AND confirmed_by LIKE 'cdp:%'"
            " ORDER BY intent_ts").fetchall()
        return [dict(r) for r in rows]


async def confirm_and_dispatch(problem_id: str, confirmed_by: str) -> dict:
    """工作台确认：原子认领 → CDP 重新填（弹窗可能已关）→ 提交 → 回读 → 落账。"""
    # 原子认领（M7：pending → committing，双击确认不会重复提交）
    c = _conn()
    now = datetime.now().isoformat(timespec="seconds")
    cur = c.execute(
        "UPDATE write_ledger SET status='committing', result_ts=?, confirmed_by=?"
        " WHERE problem_id=? AND status='pending'",
        (now, confirmed_by, problem_id))
    c.commit()
    if cur.rowcount != 1:
        c.close()
        return {"ok": False, "note": "no pending row (already claimed or committed)"}
    row = c.execute("SELECT reply_text, waybill_no FROM write_ledger WHERE problem_id=?",
                    (problem_id,)).fetchone()
    c.close()
    if not row:
        return {"ok": False, "note": "row vanished"}
    # 行锚：运单号优先（唯一），空则用回复文本前 12 字兜底
    hint = row["waybill_no"] or row["reply_text"][:12]
    # CDP：重新填（弹窗可能已关）→ 提交 → 回读
    from service.cdp_driver import confirm_flow
    result = await confirm_flow(hint, row["reply_text"])
    c = _conn()
    if result.get("ok"):
        c.execute("UPDATE write_ledger SET status='committed', result_ts=?, platform_receipt=?"
                  " WHERE problem_id=?", (now, (result.get("readback") or "")[:200], problem_id))
        c.commit(); c.close()
        audit.append("bridge_reply_committed", {
            "problem_id": problem_id, "confirmed_by": confirmed_by,
            "readback": (result.get("readback") or "")[:120], "verified": True,
        }, actor=confirmed_by)
        from db.metrics import record_committed
        record_committed(problem_id)
        return {"ok": True, "note": "committed (verified)", "readback": result.get("readback")}
    else:
        c.execute("UPDATE write_ledger SET status='unknown', result_ts=? WHERE problem_id=?",
                  (now, problem_id))
        c.commit(); c.close()
        audit.append("bridge_reply_unknown", {
            "problem_id": problem_id, "error": (result.get("error") or "")[:80],
        }, actor=confirmed_by)
        return {"ok": False, "note": result.get("error") or "submit failed"}


def _conn():
    import sqlite3
    from shared.config import DB_PATH
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    return c


