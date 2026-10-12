"""远程更新服务端（部署在家庭服务器）：版本仓库 + 签名 + 每机令牌校验。

零依赖（标准库）：HTTPServer + 手写路由。部署：
  ssh homeserver "python3 ~/update-server/server.py"
安全：
- 客户端每机独立令牌（HMAC(machine_id, bridge_secret)），服务器持 bridge_secret 可验
- 发布更新时用 publish_key 签名（只有开发者能发）
- 白名单：更新包里的路径必须落在白名单目录内
"""
import hashlib
import hmac
import json
import io
import zipfile
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from datetime import datetime

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
DATA.mkdir(exist_ok=True)
SECRET_PATH = DATA / "bridge_secret.key"     # 与客户端 config/bridge_secret.key 同值
PUBLISH_KEY_PATH = DATA / "publish_key.key"  # 开发者发布用（不发给客服）
VERSIONS = DATA / "versions"
VERSIONS.mkdir(exist_ok=True)
WHITELIST_PREFIX = ("config/", "service/", "shared/", "extension/", "api/")

for p in (SECRET_PATH, PUBLISH_KEY_PATH):
    if not p.exists():
        import secrets
        p.write_text(secrets.token_hex(32), encoding="utf-8")
        print(f"[init] generated {p.name}")


def _secret() -> str:
    return SECRET_PATH.read_text(encoding="utf-8").strip()


def _verify_machine(headers) -> bool:
    """校验客户端机器令牌：HMAC(machine_id, secret)。"""
    mid = headers.get("X-Machine-Id", "")
    sig = headers.get("X-Machine-Signature", "")
    if not mid or not sig:
        return False
    expect = hmac.new(_secret().encode(), mid.encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(sig, expect)


def _verify_publish(headers) -> bool:
    key = headers.get("X-Publish-Key", "")
    return bool(key) and hmac.compare_digest(key, PUBLISH_KEY_PATH.read_text(encoding="utf-8").strip())


def _publish_bundle(files: dict) -> bytes:
    """files: {rel_path: content_str} → zip bytes + integrity hash。"""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for rel, content in files.items():
            zf.writestr(rel, content)
    return buf.getvalue()


class Handler(BaseHTTPRequestHandler):

    def _json(self, code: int, data: dict) -> None:
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if not _verify_machine(self.headers):
            self._json(401, {"error": "machine auth failed"})
            return
        if self.path == "/version/latest":
            meta_path = VERSIONS / "latest.json"
            if not meta_path.exists():
                self._json(404, {"error": "no version published"})
                return
            self._json(200, json.loads(meta_path.read_text(encoding="utf-8")))
        elif self.path.startswith("/version/download/"):
            ver = self.path.rsplit("/", 1)[-1]
            pkg = VERSIONS / f"{ver}.zip"
            if not pkg.exists():
                self._json(404, {"error": "version not found"})
                return
            body = pkg.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "application/zip")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self._json(404, {"error": "not found"})

    def do_POST(self):
        if self.path == "/publish":
            if not _verify_publish(self.headers):
                self._json(401, {"error": "publish auth failed"})
                return
            length = int(self.headers.get("Content-Length", 0))
            req = json.loads(self.rfile.read(length).decode("utf-8"))
            version = req.get("version", "")
            files = req.get("files", {})
            if not version or not files:
                self._json(400, {"error": "version and files required"})
                return
            for rel in files:
                if not rel.startswith(WHITELIST_PREFIX):
                    self._json(400, {"error": f"path not whitelisted: {rel}"})
                    return
            payload = _publish_bundle(files)
            integrity = hashlib.sha256(payload).hexdigest()
            pkg = VERSIONS / f"{version}.zip"
            pkg.write_bytes(payload)
            (VERSIONS / "latest.json").write_text(json.dumps({
                "version": version,
                "integrity": integrity,
                "files": list(files.keys()),
                "published_at": datetime.now().isoformat(timespec="seconds"),
            }, ensure_ascii=False), encoding="utf-8")
            # 登记客户端机器
            self._json(200, {"ok": True, "version": version, "integrity": integrity[:16]})
        elif self.path == "/register":
            length = int(self.headers.get("Content-Length", 0))
            req = json.loads(self.rfile.read(length).decode("utf-8"))
            mid = req.get("machine_id", "")
            if not mid:
                self._json(400, {"error": "machine_id required"})
                return
            reg = DATA / "machines.json"
            machines = json.loads(reg.read_text(encoding="utf-8")) if reg.exists() else {}
            machines[mid] = {"registered_at": datetime.now().isoformat(timespec="seconds")}
            reg.write_text(json.dumps(machines, ensure_ascii=False, indent=1), encoding="utf-8")
            self._json(200, {"ok": True, "machine_id": mid[:8]})
        else:
            self._json(404, {"error": "not found"})

    def log_message(self, fmt, *args):
        print(f"[{datetime.now().strftime('%H:%M:%S')}] {fmt % args}")


if __name__ == "__main__":
    port = 8900
    print(f"[update-server] listening on :{port}")
    print(f"[update-server] machine secret: {SECRET_PATH}")
    print(f"[update-server] publish key:    {PUBLISH_KEY_PATH}")
    HTTPServer(("0.0.0.0", port), Handler).serve_forever()
