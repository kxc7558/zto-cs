"""压力测试：confirm_and_dispatch 的原子认领 + 后台任务取消行为。
临时 DB，不碰真实数据；CDP 用桩替换。"""
import asyncio
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

TMP = Path(tempfile.mkdtemp(prefix="zto-stress-"))
DB = TMP / "t.db"

import shared.config as scfg
scfg.DB_PATH = DB
scfg.AUDIT_DIR = TMP / "audit"
scfg.LOG_DIR = TMP / "logs"

import db.ledger as ledger
ledger.DB_PATH = DB
ledger._conn_instance = None

import service.bridge as bridge
import service.cdp_driver as cdp
import service.cdp_runner as cdp_runner

# 桩：记录调用次数，模拟慢提交（现在 CDP 走子进程，故 patch cdp_runner.run_cdp_action）
CALLS = []


async def fake_run_cdp_action(action, hint, hint2, text, timeout=None):
    CALLS.append((action, hint, hint2, text))
    await asyncio.sleep(0.3)
    return {"ok": True, "readback": "OK-" + text[:8]}


cdp_runner.run_cdp_action = fake_run_cdp_action

import shared.audit as audit
audit.append = lambda *a, **k: None
import db.metrics as metrics
metrics.record_committed = lambda *a, **k: None
bridge.audit = audit
# bridge 内部 `from db.metrics import record_committed` 是在函数里导入 → patch 模块属性
metrics_record = metrics.record_committed


def reset_db():
    if ledger._conn_instance is not None:
        try:
            ledger._conn_instance.close()
        except Exception:
            pass
    ledger._conn_instance = None
    if DB.exists():
        try:
            DB.unlink()
        except PermissionError:
            pass
    c = ledger._conn()
    return c


def seed(rows):
    """rows: list of (problem_id, idempotency_key). 插入 pending 行。"""
    c = ledger._conn()
    for pid, key in rows:
        ledger.record_intent(pid, "WB-" + key, "reply", "T-" + key, key, False,
                             "cdp:semi", ptype="x", anchor="WB-" + key, anchor2=None)


async def scenario_same_row(n=10):
    CALLS.clear()
    reset_db()
    seed([("P1", "k1")])
    row = ledger.get_by_problem("P1")[0]["id"]
    tasks = [asyncio.create_task(bridge.confirm_and_dispatch("P1", "human:workbench", row_id=row))
             for _ in range(n)]
    res = await asyncio.gather(*tasks)
    print("   notes:", [r.get("note") for r in res])
    return len(CALLS), sum(1 for r in res if r.get("ok"))


async def scenario_multi_pending_no_rowid(n=2):
    """同一 problem_id 两行 pending，并发 row_id=None → 是否重复提交到不同行。"""
    CALLS.clear()
    reset_db()
    seed([("P2", "k2a"), ("P2", "k2b")])
    tasks = [asyncio.create_task(bridge.confirm_and_dispatch("P2", "human:workbench", row_id=None))
             for _ in range(n)]
    res = await asyncio.gather(*tasks)
    return len(CALLS), sum(1 for r in res if r.get("ok"))


async def scenario_cancel():
    """提交中途取消（模拟 lifespan 关停 / 请求取消）：行是否卡在 committing。"""
    CALLS.clear()
    reset_db()
    seed([("P3", "k3")])
    row = ledger.get_by_problem("P3")[0]["id"]
    t = asyncio.create_task(bridge.confirm_and_dispatch("P3", "human:workbench", row_id=row))
    await asyncio.sleep(0.1)   # 进入 await confirm_flow
    t.cancel()
    try:
        await t
    except asyncio.CancelledError:
        pass
    except Exception as e:
        print("  cancel raised:", type(e).__name__, e)
    st = ledger.get_by_problem("P3")[0]["status"]
    return st


async def main():
    print("A) 10x concurrent same row_id:", await scenario_same_row(10), "(expect submits=1, ok=1)")
    print("B) 2x concurrent row_id=None, 2 pending rows:",
          await scenario_multi_pending_no_rowid(2), "(expect submits=0, ok=0 — 拒绝以免重复提交)")
    print("C) cancel mid-submit -> row status:", await scenario_cancel(), "(expect unknown, not committing)")


if __name__ == "__main__":
    asyncio.run(main())
