"""发布更新：本地打包 zip 并用 Ed25519 私钥签名，上传到家庭服务器。

用法：python scripts/publish.py <版本号> [--server http://100.104.230.47:8900]
签名密钥 ~/.zto-cs-signing.key（开发者本机，不进 git）。
发布密钥 ~/.zto-cs-publish.key（服务器 /publish 的访问控制）。
"""
import base64
import io
import json
import sys
import urllib.request
import zipfile
from pathlib import Path

# 家庭服务器（Tailscale/内网 IP）不走系统代理
_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

sys.path.insert(0, str(Path(__file__).resolve().parent))
from signing import sign_payload

ROOT = Path(__file__).resolve().parent.parent
PUBLISH_KEY = Path.home() / ".zto-cs-publish.key"
WHITELIST = ["config", "service", "shared", "api", "db"]
SKIP = {"__pycache__", ".venv", "node_modules", "bridge_secret.key", "machine_id",
        "VERSION", "update_pubkey.py", "access_code.txt",
        "zto-cs.db", "bridge_cache.json", "metrics.json"}  # 信任根/凭证/业务数据不进更新包


def collect_files() -> dict:
    files = {}
    for d in WHITELIST:
        base = ROOT / d
        if not base.exists():
            continue
        for p in base.rglob("*"):
            if p.is_file() and not any(s in p.parts for s in SKIP):
                rel = str(p.relative_to(ROOT)).replace("\\", "/")
                files[rel] = p.read_bytes()
    return files


def build_zip(files: dict) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for rel, content in files.items():
            zf.writestr(rel, content)
    return buf.getvalue()


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
        sys.exit(1)
    key = PUBLISH_KEY.read_text(encoding="utf-8").strip()
    files = collect_files()
    print(f"[发布] 版本 {version}，{len(files)} 个文件")
    payload = build_zip(files)
    sig = sign_payload(payload)  # Ed25519（客户端持公钥验签）
    req = urllib.request.Request(
        f"{server}/publish",
        data=json.dumps({"version": version,
                         "payload_b64": base64.b64encode(payload).decode(),
                         "signature": sig["signature"],
                         "sha256": sig["sha256"]}).encode("utf-8"),
        headers={"Content-Type": "application/json", "X-Publish-Key": key})
    r = _opener.open(req, timeout=60)
    print("[完成]", r.read().decode("utf-8"))


if __name__ == "__main__":
    main()
