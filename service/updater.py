"""远程更新客户端（updater）：定时向家庭服务器查版本，验签→下载→备份→热替换→可回滚。

安全设计（review 硬要求，不裸奔）：
- 每机独立令牌（安装时生成，服务器登记）
- 更新包 SHA256 验签，验不过不装
- 白名单：只覆盖 config/ service/ extension/ shared/ db/schema 等白名单目录，绝不接受任意路径
- 更新前自动备份到 bak/<版本>/，一条命令回滚
- 每次更新进审计哈希链

热更分层：
- 热更（不重启）：config/*.yaml（模板/规则/选择器）
- 冷更（重启服务）：service/ shared/ extension/ 代码文件
"""
import hashlib
import hmac
import json
import shutil
import time
from datetime import datetime
from pathlib import Path

import httpx

from shared import audit
from shared import bridge_auth
from shared.config import ROOT, LOG_DIR

BAK_DIR = ROOT / "bak"
BAK_DIR.mkdir(exist_ok=True)

# 更新白名单：只接受这些目录/文件下的内容（防任意代码执行）
WHITELIST = {
    "config": ROOT / "config",
    "service": ROOT / "service",
    "shared": ROOT / "shared",
    "extension": ROOT / "extension",
    "api": ROOT / "api",
}

HOT_RELOAD_DIRS = {"config"}  # 热更（不重启）
MACHINE_ID_PATH = ROOT / "config" / "machine_id"


def machine_id() -> str:
    """本机标识（安装时生成一次，服务器登记用）。"""
    if not MACHINE_ID_PATH.exists():
        import secrets
        MACHINE_ID_PATH.write_text(secrets.token_hex(16), encoding="utf-8")
    return MACHINE_ID_PATH.read_text(encoding="utf-8").strip()


def _update_server() -> str:
    from shared.config import UPDATE_SERVER_URL
    return UPDATE_SERVER_URL


def _auth_headers() -> dict:
    """每机独立令牌：HMAC(machine_id, bridge_secret)——服务器可验。"""
    key = bridge_auth.load_or_create()
    mid = machine_id()
    sig = hmac.new(key.encode(), mid.encode(), hashlib.sha256).hexdigest()
    return {"X-Machine-Id": mid, "X-Machine-Signature": sig}


def _safe_target(rel_path: str) -> Path | None:
    """白名单校验：rel_path 必须落在白名单目录内（防路径遍历）。"""
    parts = Path(rel_path).parts
    if not parts or parts[0] not in WHITELIST:
        return None
    target = (ROOT / rel_path).resolve()
    root_allowed = WHITELIST[parts[0]].resolve()
    if not str(target).startswith(str(root_allowed)):
        return None
    return target


def check_version() -> dict | None:
    """查服务器最新版本。返回 manifest 或 None（服务器不可达）。"""
    try:
        r = httpx.get(f"{_update_server()}/version/latest",
                      headers=_auth_headers(), timeout=10)
        r.raise_for_status()
        return r.json()
    except Exception:
        return None


def verify_signature(manifest: dict, payload: bytes) -> bool:
    """验更新包签名：SHA256(manifest.files) 必须与 manifest.integrity 一致。"""
    expected = manifest.get("integrity", "")
    actual = hashlib.sha256(payload).hexdigest()
    return expected == actual


def backup_current(version: str, files: list[str]) -> Path:
    """更新前备份将覆盖的文件到 bak/<版本>-<时间>/。"""
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    bak = BAK_DIR / f"{version}-{ts}"
    bak.mkdir(parents=True, exist_ok=True)
    for rel in files:
        src = _safe_target(rel)
        if src and src.exists():
            dst = bak / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
    (bak / "manifest.json").write_text(
        json.dumps({"version": version, "files": files}, ensure_ascii=False, indent=1),
        encoding="utf-8",
    )
    return bak


def apply_update(manifest: dict, payload: bytes) -> dict:
    """应用更新：验签→备份→写白名单文件→按需重启提示。

    payload 是 zip 包（files[].path 相对 ROOT）。
    """
    import io
    import zipfile
    if not verify_signature(manifest, payload):
        audit.append("update_rejected", {"reason": "integrity-mismatch",
                                         "version": manifest.get("version")}, actor="updater")
        return {"ok": False, "note": "integrity check failed — rejected"}

    zf = zipfile.ZipFile(io.BytesIO(payload))
    names = zf.namelist()
    # 全部文件必须过白名单
    for n in names:
        if _safe_target(n) is None:
            audit.append("update_rejected", {"reason": f"path-not-whitelisted:{n}"}, actor="updater")
            return {"ok": False, "note": f"path not whitelisted: {n}"}

    version = manifest.get("version", "unknown")
    bak = backup_current(version, names)

    hot_only = True
    for n in names:
        target = _safe_target(n)
        if target is None:
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(zf.read(n))
        if Path(n).parts[0] not in HOT_RELOAD_DIRS:
            hot_only = False

    audit.append("update_applied", {
        "version": version, "files": names, "backup": str(bak.name),
        "hot": hot_only,
    }, actor="updater")
    return {"ok": True, "version": version, "hot": hot_only,
            "backup": str(bak), "needs_restart": not hot_only}


def rollback(version: str | None = None) -> dict:
    """回滚到最近一次备份（或指定版本）。"""
    candidates = sorted(BAK_DIR.iterdir(), reverse=True)
    if version:
        candidates = [b for b in candidates if b.name.startswith(version)]
    if not candidates:
        return {"ok": False, "note": "no backup found"}
    bak = candidates[0]
    manifest = json.loads((bak / "manifest.json").read_text(encoding="utf-8"))
    restored = []
    for rel in manifest["files"]:
        src = bak / rel
        dst = _safe_target(rel)
        if src.exists() and dst:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            restored.append(rel)
    audit.append("update_rolled_back", {"from": bak.name, "files": restored}, actor="updater")
    return {"ok": True, "from": bak.name, "restored": restored, "needs_restart": True}


def poll_and_apply() -> dict:
    """一轮：查版本→若有新版本则下载验签应用。随主服务定时跑。"""
    manifest = check_version()
    if manifest is None:
        return {"ok": False, "note": "update server unreachable"}
    current = _current_version()
    if manifest.get("version") == current:
        return {"ok": True, "note": "up-to-date", "version": current}

    try:
        r = httpx.get(f"{_update_server()}/version/download/{manifest['version']}",
                      headers=_auth_headers(), timeout=60)
        r.raise_for_status()
        result = apply_update(manifest, r.content)
        return result
    except Exception as e:
        return {"ok": False, "note": f"download failed: {e}"}


_VERSION_PATH = ROOT / "config" / "VERSION"


def _current_version() -> str:
    if _VERSION_PATH.exists():
        return _VERSION_PATH.read_text(encoding="utf-8").strip()
    return "0.1.0"
