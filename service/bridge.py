"""桥接层：待确认队列 + 确认后 CDP 提交（服务端=脑，CDP=手）。

账本行状态机 pending → committing → committed/unknown；确认一律按行 id 操作。
闸门逻辑全复用：开关 / 敏感硬闸 / 规则 / 幂等账本。

CDP 动作走独立子进程（service/cdp_runner.py → scripts/cdp_exec.py）：
uvicorn 事件循环里 playwright click 不可靠（实测），故出进程执行并带超时。
"""
import asyncio
from datetime import datetime

from shared import audit
from db import ledger


def pending_confirmations() -> list[dict]:
    """待确认队列：已填好等人工确认的回复（工作台展示 + 确认下发）。
    confirmed_by 以 cdp: 开头的（CDP 直驱填好的半自动行）。"""
    c = _conn()
    try:
        rows = c.execute(
            "SELECT * FROM write_ledger WHERE status='pending' AND confirmed_by LIKE 'cdp:%'"
            " ORDER BY intent_ts").fetchall()
        return [dict(r) for r in rows]
    finally:
        c.close()


def claim_row(problem_id: str, confirmed_by: str, row_id: str | None = None) -> dict:
    """原子认领一行（pending → committing）。返回 {ok, rid, hint, hint2, reply_text} 或 {ok:False,note}。

    幂等保证（problem_id 级）：
    - 指定 row_id：按 id 精确认领，双击/并发只成功一次（UPDATE ... WHERE status='pending'）。
    - 未指定 row_id：仅当该 problem_id 只有一条 pending 时才认领；多于一条则拒绝并要求 row_id
      ——否则 N 个并发无 row_id 调用会各认领不同行、对同一问题件重复提交。
    行锚复用落账时的确定性锚（与自动填写同一锚）；空则退回运单号。
    """
    c = _conn()
    now = datetime.now().isoformat(timespec="seconds")
    try:
        if row_id:
            target = c.execute(
                "SELECT id FROM write_ledger WHERE id=? AND status='pending'",
                (row_id,)).fetchone()
        else:
            cands = c.execute(
                "SELECT id FROM write_ledger WHERE problem_id=? AND status='pending'"
                " ORDER BY intent_ts", (problem_id,)).fetchall()
            if len(cands) > 1:
                return {"ok": False,
                        "note": f"{len(cands)} pending rows for this problem_id — row_id required"
                                " (拒绝以免重复提交)"}
            target = cands[0] if cands else None
        if not target:
            return {"ok": False, "note": "no pending row (already claimed or committed)"}
        rid = target["id"]
        cur = c.execute(
            "UPDATE write_ledger SET status='committing', result_ts=?, confirmed_actor=?"
            " WHERE id=? AND status='pending'", (now, confirmed_by, rid))
        c.commit()
        if cur.rowcount != 1:
            return {"ok": False, "note": "no pending row (already claimed or committed)"}
        row = c.execute(
            "SELECT reply_text, waybill_no, anchor, anchor2, confirmed_by, problem_id"
            " FROM write_ledger WHERE id=?", (rid,)).fetchone()
        if not row:
            return {"ok": False, "note": "row vanished"}
        # 行锚：优先用落账时命中的确定性锚（与自动填写同一锚），空则退回运单号
        hint = row["anchor"] or row["waybill_no"] or ""
        hint2 = row["anchor2"] or ""
        if not hint:
            _mark_unknown(rid, now, "no row anchor (waybill/anchor missing)", confirmed_by)
            return {"ok": False, "note": "no row anchor — needs manual"}
        return {"ok": True, "rid": rid, "hint": hint, "hint2": hint2,
                "reply_text": row["reply_text"], "confirmed_by": confirmed_by,
                "source": row["confirmed_by"], "problem_id": row["problem_id"], "now": now}
    finally:
        c.close()


async def dispatch_row(claim: dict) -> dict:
    """对已认领的行执行 CDP 提交（独立子进程）→ 回读 → 落账。

    任何异常都兜底，绝不留 committing；取消（关停）也先把行核销为 unknown 再抛。
    """
    rid = claim["rid"]
    now = claim["now"]
    confirmed_by = claim["confirmed_by"]
    problem_id = claim["problem_id"]
    try:
        from service.cdp_runner import run_cdp_action
        result = await run_cdp_action("submit", claim["hint"], claim["hint2"],
                                      claim["reply_text"])
    except asyncio.CancelledError:
        # CancelledError 是 BaseException，except Exception 捕不到 → 显式核销再抛
        _mark_unknown(rid, now, "cancelled mid-submit (shutdown)", confirmed_by)
        raise
    except Exception as e:  # noqa: BLE001 — 兜底：绝不让异常逃逸留下 committing 行
        _mark_unknown(rid, now, str(e)[:160], confirmed_by)
        return {"ok": False, "note": f"submit raised: {str(e)[:120]}"}

    c = _conn()
    try:
        if result.get("ok"):
            c.execute("UPDATE write_ledger SET status='committed', result_ts=?, platform_receipt=?"
                      " WHERE id=?", (now, (result.get("readback") or "")[:200], rid))
            c.commit()
            audit.append("bridge_reply_committed", {
                "problem_id": problem_id, "row_id": rid, "confirmed_by": confirmed_by,
                "source": claim.get("source"),
                "readback": (result.get("readback") or "")[:120], "verified": True,
            }, actor=confirmed_by)
            from db.metrics import record_committed
            record_committed(problem_id)
            return {"ok": True, "note": "committed (verified)", "readback": result.get("readback")}
        c.execute("UPDATE write_ledger SET status='unknown', result_ts=? WHERE id=?", (now, rid))
        c.commit()
        audit.append("bridge_reply_unknown", {
            "problem_id": problem_id, "row_id": rid,
            "error": (result.get("error") or "")[:80],
        }, actor=confirmed_by)
        return {"ok": False, "note": result.get("error") or "submit failed"}
    finally:
        c.close()


async def confirm_and_dispatch(problem_id: str, confirmed_by: str,
                               row_id: str | None = None) -> dict:
    """认领 + 提交一步到位（供直接调用；HTTP 端点用 claim_row + 后台 dispatch_row）。"""
    claim = claim_row(problem_id, confirmed_by, row_id=row_id)
    if not claim.get("ok"):
        return claim
    return await dispatch_row(claim)


def _mark_unknown(row_id: str, now: str, error: str, actor: str) -> None:
    """把某行置为 unknown（提交结果未知，转人工对账），并审计。"""
    c = _conn()
    try:
        c.execute("UPDATE write_ledger SET status='unknown', result_ts=? WHERE id=?", (now, row_id))
        c.commit()
    finally:
        c.close()
    audit.append("bridge_reply_unknown", {"row_id": row_id, "error": error[:120]}, actor=actor)


def _conn():
    import sqlite3
    from shared.config import DB_PATH
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    return c
