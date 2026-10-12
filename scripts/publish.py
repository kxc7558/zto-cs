"""发布更新：把本地 zto-cs 的白名单文件打包发布到家庭服务器 update-server。

用法：python scripts/publish.py <版本号> [--server http://100.104.230.47:8900]
发布密钥从 ~/.zto-cs-publish.key 读（不在项目目录，不进 git）。
"""
import hashlib
import hmac
import json
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PUBLISH_KEY = Path.home() / ".zto-cs-publish.key"
WHITELIST = ["config", "service", "shared", "extension", "api"]
SKIP = {"__pycache__", ".venv", "node_modules", "bridge_secret.key", "machine_id", "VERSION"}


def collect_files() -> dict:
    files = {}
    for d in WHITELIST:
        base = ROOT / d
        if not base.exists():
            continue
        for p in base.rglob("*"):
            if p.is_file() and not any(s in p.parts for s in SKIP):
                rel = str(p.relative_to(ROOT)).replace("\\\\", "/")
                files[rel] = p.read_text(encoding="utf-8")
    return files


def main():
    if len(sys.argv) < 2:
        print("用法：python scripts/publish.py <版本号> [--server URL]")
        sys.exit(1)
    version = sys.argv[1]
    server = "http://100.104.230.47:8900"
    if "--server" in sys.argv:
        server = sys.argv[sys.argv.index("--server") + 1]
    if not PUBLISH_KEY.exists():
        print(f"[错误] 发布密钥不存在：{PUBLISH_KEY}")
        print("首次生成：python -c \"import secrets,pathlib; pathlib.Path.home().joinpath('.zto-cs-publish.key').write_text(secrets.token_hex(32))\"")
        print("然后把这个 key 放到服务器 data/publish_key.key（同步两边）")
        sys.exit(1)
    key = PUBLISH_KEY.read_text(encoding="utf-8").strip()
    files = collect_files()
    print(f"[发布] 版本 {version}，{len(files)} 个文件")
    req = urllib.request.Request(
        f"{server}/publish",
        data=json.dumps({"version": version, "files": files}).encode("utf-8"),
        headers={"Content-Type": "application/json", "X-Publish-Key": key})
    r = urllib.request.urlopen(req, timeout=30)
    print("[完成]", r.read().decode("utf-8"))


if __name__ == "__main__":
    main()
