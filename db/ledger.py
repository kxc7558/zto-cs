"""幂等账本（SQLite，持久化，随服务重启存活）。

写对象状态机：待提交 / 已提交 / 结果未知 / 已被替代。
commit 前先落「意图」，回读成功落「结果」；结果未知走对账核销（§15.4）。

并发安全：单例连接 + 锁（TOCTOU 修复）；连接不泄漏（进程级单例）。
"""
import sqlite3
import hashlib
import json
import threading
import uuid
from datetime import datetime
from typing import Optional

from shared.config import DB_PATH

_SCHEMA = """
CREATE TABLE IF NOT EXISTS write_ledger (
    id TEXT PRIMARY KEY,
    problem_id TEXT NOT NULL,
    waybill_no TEXT,
    action TEXT NOT NULL,             -- reply / correction / rollback
    reply_text TEXT NOT NULL,
    idempotency_key TEXT UNIQUE NOT NULL,
    status TEXT NOT NULL,             -- pending / committed / unknown / superseded / failed
    auto BOOLEAN NOT NULL DEFAULT 0,  -- 自动回复标记
    confirmed_by TEXT,                -- 人工确认人（auto 时为 system:auto）
    intent_ts TEXT,
    result_ts TEXT,
    platform_receipt TEXT,            -- 平台回执/回读结果
    content_hash TEXT,
    ptype TEXT DEFAULT "",
    anchor TEXT,                      -- 确定性行锚（运单号 / 创建人），确认时复用
    anchor2 TEXT                      -- 副锚（登记时间 / 空）
)
"""

# 进程级单例连接 + 线程锁（TOCTOU：get_status 与 record_intent 之间不再有窗口；
# ledger 全是同步函数，asyncio.Lock 不支持同步 with，用 threading.Lock）
_conn_instance: Optional[sqlite3.Connection] = None
_lock = threading.Lock()


def _conn() -> sqlite3.Connection:
    global _conn_instance
    if _conn_instance is None:
        _conn_instance = sqlite3.connect(DB_PATH, check_same_thread=False)
        _conn_instance.row_factory = sqlite3.Row
        _conn_instance.executescript(_SCHEMA)
        _migrate(_conn_instance)
    return _conn_instance


def _migrate(c: sqlite3.Connection) -> None:
    """老库补列（anchor/anchor2）：CREATE TABLE IF NOT EXISTS 不会给已存在的表加列。"""
    cols = {r["name"] for r in c.execute("PRAGMA table_info(write_ledger)").fetchall()}
    for name in ("anchor", "anchor2"):
        if name not in cols:
            c.execute(f"ALTER TABLE write_ledger ADD COLUMN {name} TEXT")
    c.commit()


def make_idempotency_key(problem_id: str, action: str, reply_text: str,
                         confirmed_by: str, supersedes: str | None = None) -> str:
    """确定性幂等键（§15.4，绝不用随机 UUID）：
    K = SHA256(规范化 JSON)。崩溃后重试能重算同一 K，才能不漏判重复。
    """
    payload = {
        "problem_id": problem_id,
        "action": action,
        "text_norm": reply_text.strip(),
        "confirmed_by": confirmed_by,
        "supersedes": supersedes or "",
        "policy_version": 1,
    }
    body = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()[:32]


def record_intent(problem_id: str, waybill_no: str | None, action: str,
                  reply_text: str, idempotency_key: str, auto: bool,
                  confirmed_by: str, ptype: str = "",
                  anchor: str | None = None, anchor2: str | None = None) -> dict:
    """commit 前先落意图（原子：INSERT OR IGNORE 按 idempotency_key 去重）。返回行。

    anchor/anchor2：填弹窗时命中的确定性行锚（运单号优先，否则创建人+登记时间），
    落进账本供工作台确认时复用同一锚（避免确认路径重新猜锚对不上行）。
    """
    now = datetime.now().isoformat(timespec="seconds")
    content_hash = hashlib.sha256(reply_text.strip().encode("utf-8")).hexdigest()[:16]
    wid = uuid.uuid5(uuid.NAMESPACE_URL, idempotency_key).hex[:16]
    c = _conn()
    with _lock:
        existing = c.execute(
            "SELECT * FROM write_ledger WHERE idempotency_key=?", (idempotency_key,)
        ).fetchone()
        if existing:
            return dict(existing)
        c.execute(
            "INSERT OR IGNORE INTO write_ledger (id, problem_id, waybill_no, action, reply_text,"
            " idempotency_key, status, auto, confirmed_by, intent_ts, content_hash, ptype,"
            " anchor, anchor2)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (wid, problem_id, waybill_no, action, reply_text, idempotency_key,
             "pending", int(auto), confirmed_by, now, content_hash, ptype, anchor, anchor2),
        )
        c.commit()
    return {"id": wid, "status": "pending", "idempotency_key": idempotency_key}


def delete_intent(idempotency_key: str) -> int:
    """删除一条意图（填弹窗失败时回撤 pending 行）。返回删除行数。"""
    c = _conn()
    with _lock:
        cur = c.execute("DELETE FROM write_ledger WHERE idempotency_key=?", (idempotency_key,))
        c.commit()
        return cur.rowcount


def reconcile_committing() -> list[dict]:
    """崩溃/重启恢复：把遗留的 committing 行核销为 unknown（诚实：提交可能已发出，
    结果未知 → 转人工对账，绝不假装完成，也绝不永久卡死）。

    仅在进程启动时调用（此刻本进程没有任何在途提交），因此不会误伤正在提交的行。
    """
    now = datetime.now().isoformat(timespec="seconds")
    c = _conn()
    with _lock:
        rows = c.execute(
            "SELECT id, problem_id FROM write_ledger WHERE status='committing'"
        ).fetchall()
        if rows:
            c.execute(
                "UPDATE write_ledger SET status='unknown', result_ts=?"
                " WHERE status='committing'", (now,))
            c.commit()
    return [dict(r) for r in rows]


def record_result(idempotency_key: str, status: str,
                  platform_receipt: str | None = None) -> None:
    """回读成功后落结果。status: committed / unknown / failed / superseded"""
    now = datetime.now().isoformat(timespec="seconds")
    c = _conn()
    with _lock:
        c.execute(
            "UPDATE write_ledger SET status=?, result_ts=?, platform_receipt=?"
            " WHERE idempotency_key=?",
            (status, now, platform_receipt, idempotency_key),
        )
        c.commit()


def get_status(idempotency_key: str) -> Optional[dict]:
    c = _conn()
    with _lock:
        row = c.execute(
            "SELECT * FROM write_ledger WHERE idempotency_key=?", (idempotency_key,)
        ).fetchone()
        return dict(row) if row else None


def get_by_problem(problem_id: str) -> list[dict]:
    """某问题件的全部写记录（含版本链）。"""
    c = _conn()
    with _lock:
        rows = c.execute(
            "SELECT * FROM write_ledger WHERE problem_id=? ORDER BY intent_ts",
            (problem_id,),
        ).fetchall()
        return [dict(r) for r in rows]


def latest_committed(problem_id: str) -> Optional[dict]:
    c = _conn()
    with _lock:
        row = c.execute(
            "SELECT * FROM write_ledger WHERE problem_id=? AND status='committed'"
            " ORDER BY result_ts DESC LIMIT 1",
            (problem_id,),
        ).fetchone()
        return dict(row) if row else None
