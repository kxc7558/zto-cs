"""zto-cs 下载服务（家庭服务器 8901）：客服浏览器直接下载安装包。零依赖。"""
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
import urllib.parse
from datetime import datetime

FILES_DIR = Path(__file__).resolve().parent / "downloads"
FILES_DIR.mkdir(exist_ok=True)

PAGE = """<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="UTF-8"><title>中通客服智助 - 下载</title>
<style>body{font-family:"Microsoft YaHei",sans-serif;max-width:640px;margin:60px auto;padding:0 20px;
color:#1f2329;line-height:1.8}
.btn{display:block;background:#3370ff;color:#fff;text-align:center;padding:16px;border-radius:10px;
text-decoration:none;font-size:18px;font-weight:600;margin:24px 0}
.steps{background:#f6f7f9;border-radius:12px;padding:20px 28px;font-size:14px}
code{background:#eef0f3;padding:2px 8px;border-radius:4px}</style></head>
<body>
<h1>中通客服智助 · 问题件助手</h1>
<p>自动帮您盯问题件、准备回复。安装只需 2 分钟。</p>
<a class="btn" href="/download/zto-cs-setup.zip">下载安装包</a>
<div class="steps">
<b>安装步骤：</b><br>
1. 下载后<b>解压</b>到任意文件夹<br>
2. 进入文件夹，双击 <code>安装.bat</code>（自动装完一切）<br>
3. 双击 <code>启动浏览器.bat</code>，登录中通（安盾/微信扫码，只需一次）<br>
4. 打开工作台：<code>http://127.0.0.1:8765</code>，输入访问码（安装完成后屏幕上会显示）<br>
<br>
<b>之后日常使用：</b>开机自动启动。有问题件系统自动填好回复，您确认后发送。
</div>
<p style="color:#86909c;font-size:12px">版本 0.3.0 · 更新时间 {UPDATED}</p>
</body></html>"""


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, content, ctype="text/html; charset=utf-8"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def do_GET(self):
        path = urllib.parse.unquote(self.path)
        if path in ("/", "/index.html"):
            files = sorted(FILES_DIR.glob("*.zip"), key=lambda p: p.stat().st_mtime, reverse=True)
            upd = datetime.fromtimestamp(files[0].stat().st_mtime).strftime("%Y-%m-%d %H:%M") if files else "-"
            self._send(200, PAGE.replace("{UPDATED}", upd).encode("utf-8"))
        elif path.startswith("/download/"):
            name = path.rsplit("/", 1)[-1]
            f = FILES_DIR / name
            if not f.exists():
                self._send(404, b"not found", "text/plain")
                return
            self.send_response(200)
            self.send_header("Content-Type", "application/zip")
            self.send_header("Content-Disposition", f'attachment; filename="{name}"')
            self.send_header("Content-Length", str(f.stat().st_size))
            self.end_headers()
            self.wfile.write(f.read_bytes())
        else:
            self._send(404, b"not found", "text/plain")

    def log_message(self, fmt, *args):
        print(f"[{datetime.now().strftime('%H:%M:%S')}] {fmt % args}")


if __name__ == "__main__":
    print("[download-server] listening on :8901")
    HTTPServer(("0.0.0.0", 8901), Handler).serve_forever()
