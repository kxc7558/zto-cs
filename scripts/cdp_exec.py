"""CDP 执行器（独立子进程）：uvicorn 事件循环里 playwright click 不可靠（实测），
CDP 动作放独立进程跑。用法：
  python scripts/cdp_exec.py fill <hint> <hint2> <reply_text_file>
  python scripts/cdp_exec.py submit <hint> <hint2> <reply_text_file>
输出 JSON 到 stdout。
"""
import sys
import json
import asyncio
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


async def main():
    action = sys.argv[1]
    hint, hint2 = sys.argv[2], sys.argv[3]
    text_file = sys.argv[4]
    reply_text = Path(text_file).read_text(encoding="utf-8")

    from service.cdp_driver import fill_for_confirm, fill_and_submit
    if action == "fill":
        r = await fill_for_confirm(hint=hint, hint2=hint2, reply_text=reply_text)
    else:
        r = await fill_and_submit(hint=hint, hint2=hint2, reply_text=reply_text)
    print(json.dumps(r, ensure_ascii=False))


asyncio.run(main())
