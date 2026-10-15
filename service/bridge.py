"""桥接层：待确认队列 + 确认后 CDP 提交（服务端=脑，CDP=手）。

账本行状态机 pending → committing → committed/unknown；确认一律按行 id 操作。
闸门逻辑全复用：开关 / 敏感硬闸 / 规则 / 幂等账本。
"""
import sys
import os
import json
from pathlib import Path
from datetime import datetime

import asyncio

from shared import audit
from shared.models import ProblemParcel
from service import rules
from service.problem_reader import _make_problem_id
from db import ledger


def pending_confirmations() -> list[dict]:
    """待确认队列：已填好等人工确认的回复（工作台展示 + 确认下发）。
    confirmed_by 以 cdp: 开头的（CDP 直驱填好的半自动行）。"""
    with _conn() as c:
        rows = c.execute(
            "SELECT * FROM write_ledger WHERE status='pending' AND confirmed_by LIKE 'cdp:%'"
            " ORDER BY intent_ts").fetchall()
        return [dict(r) for r in rows]


async def confirm_and_dispatch(problem_id: str, confirmed_by: str,
                               row_id: str | None = None) -> dict:
    """工作台确认：原子认领具体行 → CDP 重新填（弹窗可能已关）→ 提交 → 回读 → 落账。

    两处关键修复：
    - 一律按行 id（而非 problem_id）读写：同一 problem_id 可能有多行（cdp/human 幂等键
      不同），按 problem_id 会取错行、批量改错行。
    - 整体 try/except：CDP 抛异常（playwright 未装/页未开/行不匹配）也必须兜底，
      绝不把行永久卡在 committing。
    """
    # 原子认领（M7：pending → committing，双击确认不会重复提交）。按 id 精确定位。
    c = _conn()
    now = datetime.now().isoformat(timespec="seconds")
    if row_id:
        target = c.execute(
            "SELECT id FROM write_ledger WHERE id=? AND status='pending'", (row_id,)).fetchone()
    else:
        # 未指行：认领该 problem_id 最早的 pending 行（向后兼容）
        target = c.execute(
            "SELECT id FROM write_ledger WHERE problem_id=? AND status='pending'"
            " ORDER BY intent_ts LIMIT 1", (problem_id,)).fetchone()
    if not target:
        c.close()
        return {"ok": False, "note": "no pending row (already claimed or committed)"}
    rid = target["id"]
    cur = c.execute(
        "UPDATE write_ledger SET status='committing', result_ts=?, confirmed_by=?"
        " WHERE id=? AND status='pending'", (now, confirmed_by, rid))
    c.commit()
    if cur.rowcount != 1:
        c.close()
        return {"ok": False, "note": "no pending row (already claimed or committed)"}
    row = c.execute(
        "SELECT reply_text, waybill_no, anchor, anchor2 FROM write_ledger WHERE id=?",
        (rid,)).fetchone()
    c.close()
    if not row:
        return {"ok": False, "note": "row vanished"}
    # 行锚：优先用落账时命中的确定性锚（与自动填写同一锚），空则退回运单号
    hint = row["anchor"] or row["waybill_no"] or ""
    hint2 = row["anchor2"] or ""
    if not hint:
        _mark_unknown(rid, now, "no row anchor (waybill/anchor missing)", confirmed_by)
        return {"ok": False, "note": "no row anchor — needs manual"}
    # CDP：重新填（弹窗可能已关）→ 提交 → 回读。任何异常都兜底，不留 committing。
    try:
        from service.cdp_driver import confirm_flow
        result = await confirm_flow(hint, hint2, row["reply_text"])
    except Exception as e:  # noqa: BLE001 — 兜底：绝不让异常逃逸留下 committing 行
        _mark_unknown(rid, now, str(e)[:160], confirmed_by)
        return {"ok": False, "note": f"submit raised: {str(e)[:120]}"}
    c = _conn()
    if result.get("ok"):
        c.execute("UPDATE write_ledger SET status='committed', result_ts=?, platform_receipt=?"
                  " WHERE id=?", (now, (result.get("readback") or "")[:200], rid))
        c.commit(); c.close()
        audit.append("bridge_reply_committed", {
            "problem_id": problem_id, "row_id": rid, "confirmed_by": confirmed_by,
            "readback": (result.get("readback") or "")[:120], "verified": True,
        }, actor=confirmed_by)
        from db.metrics import record_committed
        record_committed(problem_id)
        return {"ok": True, "note": "committed (verified)", "readback": result.get("readback")}
    else:
        c.execute("UPDATE write_ledger SET status='unknown', result_ts=? WHERE id=?", (now, rid))
        c.commit(); c.close()
        audit.append("bridge_reply_unknown", {
            "problem_id": problem_id, "row_id": rid,
            "error": (result.get("error") or "")[:80],
        }, actor=confirmed_by)
        return {"ok": False, "note": result.get("error") or "submit failed"}


def _mark_unknown(row_id: str, now: str, error: str, actor: str) -> None:
    """把某行置为 unknown（提交结果未知，转人工对账），并审计。"""
    c = _conn()
    c.execute("UPDATE write_ledger SET status='unknown', result_ts=? WHERE id=?", (now, row_id))
    c.commit(); c.close()
    audit.append("bridge_reply_unknown", {"row_id": row_id, "error": error[:120]}, actor=actor)


def _conn():
    import sqlite3
    from shared.config import DB_PATH
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    return c


