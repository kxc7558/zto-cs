"""同时启动主工作台（127.0.0.1:8765）与只读入口（0.0.0.0:8766）。"""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PY = sys.executable

procs = [
    subprocess.Popen([PY, "-m", "uvicorn", "api.main:app", "--host", "127.0.0.1", "--port", "8765"], cwd=ROOT),
    subprocess.Popen([PY, "-m", "uvicorn", "api.readonly:ro_app", "--host", "0.0.0.0", "--port", "8766"], cwd=ROOT),
]
try:
    procs[0].wait()
finally:
    for p in procs:
        p.terminate()
