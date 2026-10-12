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
    """校验客户端机器令牌：HMAC(machine_id:timestamp, secret) + 时间窗（防重放）。"""
    mid = headers.get("X-Machine-Id", "")
    ts = headers.get("X-Machine-Timestamp", "")
    sig = headers.get("X-Machine-Signature", "")
    if not mid or not ts or not sig:
        return False
    # 时间窗 ±5 分钟（review HIGH-6：无时间戳可无限重放）
    import time as _time
    try:
        if abs(_time.time() - int(ts)) > 300:
            return False
    except ValueError:
        return False
    expect = hmac.new(_secret().encode(), f"{mid}:{ts}".encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(sig, expect)


def _verify_publish(headers) -> bool:
    key = headers.get("X-Publish-Key", "")
    return bool(key) and hmac.compare_digest(key, PUBLISH_KEY_PATH.read_text(encoding="utf-8").strip())


def _path_whitelisted(rel: str) -> bool:
    """路径感知白名单（review MEDIUM-8：字符串前缀会被 config/../service/x 绕过）。"""
    parts = Path(rel).parts
    if not parts:
        return False
    top = parts[0]
    if top not in ("config", "service", "shared", "extension", "api"):
        return False
    # 禁路径遍历、绝对路径、盘符
    if ".." in parts or rel.startswith(("/", "\\")) or ":" in rel:
        return False
    return True


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
            import base64
            length = int(self.headers.get("Content-Length", 0))
            req = json.loads(self.rfile.read(length).decode("utf-8"))
            version = req.get("version", "")
            payload_b64 = req.get("payload_b64", "")
            sig_hex = req.get("signature", "")
            if not version or not payload_b64 or not sig_hex:
                self._json(400, {"error": "version, payload_b64, signature required"})
                return
            payload = base64.b64decode(payload_b64)
            # 白名单校验（客户端也验，双重）
            import zipfile as _zf
            for n in _zf.ZipFile(io.BytesIO(payload)).namelist():
                if not _path_whitelisted(n):
                    self._json(400, {"error": f"path not whitelisted: {n}"})
                    return
            sha_hex = hashlib.sha256(payload).hexdigest()
            pkg = VERSIONS / f"{version}.zip"
            pkg.write_bytes(payload)
            (VERSIONS / "latest.json").write_text(json.dumps({
                "version": version,
                "signature": sig_hex,
                "sha256": sha_hex,
                "published_at": datetime.now().isoformat(timespec="seconds"),
            }, ensure_ascii=False), encoding="utf-8")
            self._json(200, {"ok": True, "version": version, "sha256": sha_hex[:16]})
        elif self.path == "/register":
            if not _verify_publish(self.headers):
                self._json(401, {"error": "register requires publish key"})
                return
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
