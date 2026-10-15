"""CDP 执行器客户端：把 CDP 动作放到独立子进程跑。

背景：uvicorn 事件循环里 playwright click 不可靠（实测），故 CDP 动作必须出进程。
本模块是唯一调用方入口，负责：
- 写临时文本文件（finally 清理，绝不泄漏）
- 带超时地跑子进程（超时即杀，绝不让提交永久挂起）
- 结构化解析子进程 stdout JSON：区分「子进程崩溃」与「业务失败返回」
  （子进程自身也用 JSON 回错误，见 scripts/cdp_exec.py）
"""
import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_EXEC = ROOT / "scripts" / "cdp_exec.py"

VALID_ACTIONS = ("fill", "submit")
FILL_TIMEOUT = 120.0     # 填弹窗：含反风控等待 + 重试
SUBMIT_TIMEOUT = 180.0   # 提交：填 + 提交 + 回读，留足


async def run_cdp_action(action: str, hint: str, hint2: str, reply_text: str,
                         timeout: float | None = None) -> dict:
    """在独立子进程执行一次 CDP 动作，返回结构化 dict（ok/error/...）。"""
    if action not in VALID_ACTIONS:
        return {"ok": False, "error": f"invalid cdp action: {action}"}
    if timeout is None:
        timeout = FILL_TIMEOUT if action == "fill" else SUBMIT_TIMEOUT

    tmp_path = None
    try:
        fd, tmp_path = tempfile.mkstemp(prefix="zto-cdp-", suffix=".txt")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(reply_text or "")
        proc = await asyncio.create_subprocess_exec(
            sys.executable, str(_EXEC), action, hint or "", hint2 or "", tmp_path,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        try:
            out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            _kill(proc)
            return {"ok": False, "error": f"cdp {action} timeout after {int(timeout)}s"}
        stdout = out.decode("utf-8", "replace").strip()
        stderr = err.decode("utf-8", "replace").strip()
        if not stdout:
            # 子进程崩了（无结构化输出）→ 明确区分于业务失败
            return {"ok": False,
                    "error": f"cdp exec no output (rc={proc.returncode}): {stderr[:160]}"}
        try:
            return json.loads(stdout)
        except json.JSONDecodeError:
            return {"ok": False, "error": f"cdp exec bad JSON: {stdout[:160]}"}
    finally:
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass


def _kill(proc) -> None:
    try:
        proc.kill()
    except ProcessLookupError:
        return
    except Exception:  # noqa: BLE001
        pass
