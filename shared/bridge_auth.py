"""桥接鉴权：插件↔API 共享密钥（本地文件，插件与服务器各持一份）。

生成一次，两边同步；不进 git（.gitignore）。本机 127.0.0.1 + 密钥双保险：
- 端口绑定只防外部，不防本机其它进程——密钥补上这层。
- 插件把密钥存 chrome.storage.local（首次手动粘贴配置）。
"""
import hashlib
import hmac
import secrets
from pathlib import Path

from shared.config import ROOT

KEY_PATH = ROOT / "config" / "bridge_secret.key"  # .gitignore


def load_or_create() -> str:
    if KEY_PATH.exists():
        return KEY_PATH.read_text(encoding="utf-8").strip()
    key = secrets.token_hex(32)
    KEY_PATH.write_text(key, encoding="utf-8")
    return key


def verify(token: str | None) -> bool:
    """校验请求头 X-Bridge-Token。"""
    if not token:
        return False
    return hmac.compare_digest(token, load_or_create())


def key_fingerprint() -> str:
    """给插件配置页展示的指纹（前 8 位，不是密钥本身）。"""
    return hashlib.sha256(load_or_create().encode()).hexdigest()[:8]
