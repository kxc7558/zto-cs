"""压力测试：审计哈希链跨进程并发写入（8765 写 / 8766 只读端也会写）。

复现原 BLOCK：无跨进程锁时 N 个进程并发 append → 链出现重复前驱 → verify 失败。
跑法：.venv/Scripts/python -I tests/stress_audit.py
"""
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TMP = Path(tempfile.mkdtemp(prefix="zto-audit-"))
N_PROC = 6
N_EACH = 40

_WORKER = """
import sys
from pathlib import Path
sys.path.insert(0, r"{root}")
from shared import audit
audit.AUDIT_DIR = Path(r"{tmp}")
for i in range({n}):
    audit.append("w", {{"i": i}}, actor="p")
"""

_VERIFY = """
import sys
from pathlib import Path
sys.path.insert(0, r"{root}")
from shared import audit
audit.AUDIT_DIR = Path(r"{tmp}")
print(audit.verify())
"""


def main():
    procs = [
        subprocess.Popen([sys.executable, "-I", "-c",
                          _WORKER.format(root=ROOT, tmp=TMP, n=N_EACH)])
        for _ in range(N_PROC)
    ]
    rcs = [p.wait() for p in procs]
    out = subprocess.check_output(
        [sys.executable, "-I", "-c", _VERIFY.format(root=ROOT, tmp=TMP)]).decode("utf-8")
    print("worker rcs:", rcs)
    print("verify:", out.strip())
    v = eval(out.strip())
    expect = N_PROC * N_EACH
    ok = v.get("ok") and v.get("records") == expect
    print(f"expected records={expect}, actual={v.get('records')}")
    print("RESULT:", "OK" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
