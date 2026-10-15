"""压力测试：ledger 单例锁 + 跨连接 SQLite 争用。"""
import sys, threading, tempfile, sqlite3
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

TMP = Path(tempfile.mkdtemp(prefix="zto-stress2-"))
DB = TMP / "t.db"

import shared.config as scfg
scfg.DB_PATH = DB
import db.ledger as ledger
ledger.DB_PATH = DB
ledger._conn_instance = None

# 1) 真实 DB 的 journal mode
real = Path(r"D:\zto-cs\db\zto-cs.db")
if real.exists():
    c = sqlite3.connect(real)
    print("real db journal_mode =", c.execute("PRAGMA journal_mode").fetchone()[0])
    print("real db busy_timeout  =", c.execute("PRAGMA busy_timeout").fetchone()[0])
    c.close()

# 2) 多线程并发 record_intent（走单例锁）
ledger._conn()
errs = []
def worker(i):
    try:
        ledger.record_intent("P%d" % i, "WB%d" % i, "reply", "t%d" % i, "key%d" % i,
                             False, "cdp:semi", ptype="x")
    except Exception as e:
        errs.append("%s: %s" % (type(e).__name__, e))
ts = [threading.Thread(target=worker, args=(i,)) for i in range(30)]
[t.start() for t in ts]
[t.join() for t in ts]
n = ledger._conn().execute("SELECT COUNT(*) FROM write_ledger").fetchone()[0]
print("threaded record_intent: rows=%d errs=%d %s" % (n, len(errs), errs[:3]))

# 3) 跨连接争用：A 持写事务，B 同库 UPDATE（无 WAL / 无 busy_timeout）
a = sqlite3.connect(DB)
b = sqlite3.connect(DB)
try:
    a.execute("BEGIN")
    a.execute("INSERT INTO write_ledger (id,problem_id,action,reply_text,idempotency_key,status,auto,confirmed_by,intent_ts)"
              " VALUES ('x','P','reply','t','kx','pending',0,'cdp:semi','now')")
    try:
        b.execute("UPDATE write_ledger SET status='committing' WHERE id='x'")
        b.commit()
        print("cross-conn concurrent write: OK (no error)")
    except Exception as e:
        print("cross-conn concurrent write: %s: %s" % (type(e).__name__, e))
finally:
    a.rollback(); a.close(); b.close()
