"""远程更新客户端（updater）：定时向家庭服务器查版本，验签→下载→备份→热替换→可回滚。

安全设计（review 硬要求，不裸奔）：
- 每机独立令牌（安装时生成，服务器登记）
- 更新包 SHA256 验签，验不过不装
- 白名单：只覆盖 config/ service/ shared/ api/ db/*.py 等白名单目录，绝不接受任意路径
- 更新前自动备份到 bak/<版本>/，一条命令回滚
- 每次更新进审计哈希链
- 只升不降：服务器版本不新于本机时拒绝安装（防降级覆盖）

热更分层：
- 热更（不重启）：config/*.yaml（模板/规则/选择器）
- 冷更（重启服务）：service/ shared/ api/ db/ 代码文件
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

# 家庭服务器（Tailscale/内网）不走系统代理
_trust_env = False
from shared import bridge_auth
from shared.config import ROOT, LOG_DIR

BAK_DIR = ROOT / "bak"
BAK_DIR.mkdir(exist_ok=True)

# 更新白名单：只接受这些目录/文件下的内容（防任意代码执行）
WHITELIST = {
    "config": ROOT / "config",
    "service": ROOT / "service",
    "shared": ROOT / "shared",
    "api": ROOT / "api",
    "db": ROOT / "db",
}

# 更新包绝不覆盖的文件（凭证/身份/业务数据——review HIGH-5）
NEVER_OVERWRITE = {"config/bridge_secret.key", "config/machine_id", "config/VERSION",
                   "config/access_code.txt",
                   "shared/update_pubkey.py",          # 信任根不能被更新包替换
                   "db/zto-cs.db", "db/bridge_cache.json", "db/metrics.json"}  # 业务数据不覆盖

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
    """每机独立令牌：HMAC(machine_id, bridge_secret) + 时间戳（防重放）。"""
    key = bridge_auth.load_or_create()
    mid = machine_id()
    ts = str(int(time.time()))
    sig = hmac.new(key.encode(), f"{mid}:{ts}".encode(), hashlib.sha256).hexdigest()
    return {"X-Machine-Id": mid, "X-Machine-Timestamp": ts, "X-Machine-Signature": sig}


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
                      headers=_auth_headers(), timeout=10, trust_env=_trust_env)
        r.raise_for_status()
        return r.json()
    except Exception:
        return None


def verify_signature(manifest: dict, payload: bytes) -> bool:
    """验更新包 Ed25519 签名（客户端持开发者公钥，HTTP 明文不影响安全性）。

    修复 review CRITICAL-2：裸 SHA256 是校验和不是签名——MITM 可同时替换
    manifest 和 payload。现在必须公钥验签 + sha256 双重校验，改一字节必失败。
    """
    from shared.update_pubkey import ED25519_PUBKEY
    from nacl.signing import VerifyKey
    from nacl.encoding import HexEncoder
    from nacl.exceptions import BadSignatureError
    sig_hex = manifest.get("signature", "")
    sha_hex = manifest.get("sha256", "")
    if not sig_hex or not sha_hex:
        return False
    # 双重校验：先 sha256（快速失败），再 Ed25519（真签名）
    if hashlib.sha256(payload).hexdigest() != sha_hex:
        return False
    try:
        VerifyKey(ED25519_PUBKEY, encoder=HexEncoder).verify(payload, bytes.fromhex(sig_hex))
        return True
    except (BadSignatureError, ValueError):
        return False


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
    # 全部文件必须过白名单 + 不许覆盖凭证/信任根
    # 归一化后比对（review CRITICAL-2：config/./bridge_secret.key 会被字符串精确匹配漏掉）
    never_norm = {str(Path(n).as_posix()) for n in NEVER_OVERWRITE}
    for n in names:
        if _safe_target(n) is None:
            audit.append("update_rejected", {"reason": f"path-not-whitelisted:{n}"}, actor="updater")
            return {"ok": False, "note": f"path not whitelisted: {n}"}
        norm = str(Path(n).as_posix())
        if norm in never_norm or norm.replace("./", "") in never_norm:
            audit.append("update_rejected", {"reason": f"credential-overwrite:{n}"}, actor="updater")
            return {"ok": False, "note": f"cannot overwrite credential: {n}"}

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
    # 写版本戳（review HIGH-3：否则 poll_and_apply 无限重装同一版本）
    _VERSION_PATH.write_text(version, encoding="utf-8")
    return {"ok": True, "version": version, "hot": hot_only,
            "backup": str(bak), "needs_restart": not hot_only}


def rollback(version: str | None = None) -> dict:
    """回滚到最近一次备份（或指定版本）。按目录内的时间戳排序（版本号字典序不可靠）。"""
    candidates = sorted(BAK_DIR.iterdir(), key=lambda b: b.name.split("-", 1)[-1], reverse=True)
    if version:
        candidates = [b for b in candidates if b.name.startswith(version)]
    if not candidates:
        return {"ok": False, "note": "no backup found"}
    bak = candidates[0]
    manifest = json.loads((bak / "manifest.json").read_text(encoding="utf-8"))
    restored = []
    for rel in manifest["files"]:
        if rel in NEVER_OVERWRITE:
            continue  # 凭证/信任根不随回滚恢复
        src = bak / rel
        dst = _safe_target(rel)
        if src.exists() and dst:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            restored.append(rel)
    audit.append("update_rolled_back", {"from": bak.name, "files": restored}, actor="updater")
    return {"ok": True, "from": bak.name, "restored": restored, "needs_restart": True}


def _version_tuple(v: str) -> tuple:
    """版本号 → 可比较元组（数字段按整数比，非数字段按字符串兜底）。

    "0.2.0" > "0.1.2-test"：取每段开头的数字，非数字后缀不计入大小（仅作并列）。
    """
    parts = []
    for seg in str(v).split("."):
        num = ""
        for ch in seg:
            if ch.isdigit():
                num += ch
            else:
                break
        parts.append(int(num) if num else 0)
    return tuple(parts)


def _is_newer(server: str, current: str) -> bool:
    """服务器版本是否严格新于当前版本（相等或更旧一律不装，杜绝降级覆盖）。"""
    return _version_tuple(server) > _version_tuple(current)


def poll_and_apply() -> dict:
    """一轮：查版本→若服务器版本更新则下载验签应用（绝不降级覆盖）。随主服务定时跑。"""
    manifest = check_version()
    if manifest is None:
        return {"ok": False, "note": "update server unreachable"}
    current = _current_version()
    server_v = manifest.get("version", "")
    if server_v == current:
        return {"ok": True, "note": "up-to-date", "version": current}
    if not _is_newer(server_v, current):
        # 服务器版本更旧或不可比 —— 拒绝，防止把客户机降级覆盖
        return {"ok": True, "note": "server version not newer — skipped",
                "version": current, "server_version": server_v}

    try:
        r = httpx.get(f"{_update_server()}/version/download/{manifest['version']}",
                      headers=_auth_headers(), timeout=60, trust_env=_trust_env)
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
