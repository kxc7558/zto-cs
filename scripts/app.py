"""桌面 App 启动器（pywebview）：客服双击快捷方式 = 桌面窗口（不是浏览器标签页）。

- 启动 uvicorn（8765）+ 等 health 就绪
- 打开 pywebview 窗口（原生 WebView2，像成熟 App）
- 窗口关闭 = 服务继续跑（后台常驻，下次打开秒开）
"""
import threading
import time
import sys
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def start_uvicorn():
    subprocess.run(
        [sys.executable, "-m", "uvicorn", "api.main:app", "--host", "127.0.0.1", "--port", "8765"],
        cwd=str(ROOT))


def wait_ready(timeout=30):
    import urllib.request
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            urllib.request.urlopen("http://127.0.0.1:8765/health", timeout=2)
            return True
        except Exception:
            time.sleep(0.5)
    return False


def main():
    import webview
    # 后台起 uvicorn
    threading.Thread(target=start_uvicorn, daemon=True).start()
    if not wait_ready():
        print("服务启动失败（端口占用或依赖缺失）")
        return
    # 桌面窗口
    webview.create_window(
        "问题件助手 · 中通客服智助",
        "http://127.0.0.1:8765",
        width=900, height=700,
        min_size=(760, 560),
    )
    webview.start()


if __name__ == "__main__":
    main()
