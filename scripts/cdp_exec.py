"""CDP 执行器（独立子进程）：uvicorn 事件循环里 playwright click 不可靠（实测），
CDP 动作放独立进程跑。用法：
  python scripts/cdp_exec.py fill   <hint> <hint2> <reply_text_file>
  python scripts/cdp_exec.py submit <hint> <hint2> <reply_text_file>

约定（供 service/cdp_runner.py 解析）：
- 无论成功失败，只往 stdout 打一行 JSON（{"ok": bool, ...}），绝不裸抛 traceback。
- action 白名单校验：非 fill/submit 一律拒绝并退出（绝不"默认落到提交"）。
- 读入的文本文件读后即删（避免调用方用 tempfile 时泄漏）。
"""
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

VALID_ACTIONS = ("fill", "submit")


def _emit(obj: dict, code: int) -> None:
    print(json.dumps(obj, ensure_ascii=False))
    sys.exit(code)


async def _run(action: str, hint: str, hint2: str, reply_text: str) -> dict:
    from service.cdp_driver import fill_for_confirm, fill_and_submit
    if action == "fill":
        return await fill_for_confirm(hint=hint, hint2=hint2, reply_text=reply_text)
    return await fill_and_submit(hint=hint, hint2=hint2, reply_text=reply_text)


def main() -> None:
    if len(sys.argv) < 5:
        _emit({"ok": False,
               "error": "usage: cdp_exec.py <fill|submit> <hint> <hint2> <text_file>"}, 2)
    action = sys.argv[1]
    if action not in VALID_ACTIONS:
        # 危险默认修复：拼写错误绝不落到真实提交
        _emit({"ok": False,
               "error": f"invalid action {action!r} (allowed: {', '.join(VALID_ACTIONS)})"}, 2)
    hint, hint2, text_file = sys.argv[2], sys.argv[3], sys.argv[4]

    try:
        reply_text = Path(text_file).read_text(encoding="utf-8")
    except Exception as e:  # noqa: BLE001
        _emit({"ok": False, "error": f"read text file failed: {type(e).__name__}: {e}"}, 1)
    finally:
        try:
            Path(text_file).unlink()
        except OSError:
            pass

    try:
        r = asyncio.run(_run(action, hint, hint2, reply_text))
    except Exception as e:  # noqa: BLE001 —— 结构化错误，绝不裸 traceback
        _emit({"ok": False, "error": f"{type(e).__name__}: {str(e)[:200]}"}, 1)

    if not isinstance(r, dict):
        _emit({"ok": False, "error": f"unexpected result type: {type(r).__name__}"}, 1)
    _emit(r, 0 if r.get("ok") else 1)


if __name__ == "__main__":
    main()
