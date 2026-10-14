"""鉴权：工作台/只读入口的访问码 + 远程更新的每机令牌根。

- 访问码（config/access_code.txt，短码）：客服在工作台首屏输入一次，存 localStorage；
  写端点（8765）与只读端点（8766）都校验它。安装时打印，管理员也留存一份。
- 令牌根（config/bridge_secret.key，64 hex）：只用于远程更新的每机 HMAC，
  绝不发给客服、不进日志、不进 git。服务端 update-server 持同值。
"""
import hashlib
import hmac
import secrets
from pathlib import Path

from shared.config import ROOT

KEY_PATH = ROOT / "config" / "bridge_secret.key"      # .gitignore —— 每机令牌根（远程更新 HMAC）
ACCESS_CODE_PATH = ROOT / "config" / "access_code.txt"  # .gitignore —— 工作台/只读 访问码（短码）

# 去易混字符（0/O/1/I/L）的访问码字母表：客服手输不易错
_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
_CODE_LEN = 8


def load_or_create() -> str:
    """每机令牌根（64 hex）：远程更新 HMAC 用，绝不发给客服、不进日志。"""
    if KEY_PATH.exists():
        return KEY_PATH.read_text(encoding="utf-8").strip()
    key = secrets.token_hex(32)
    KEY_PATH.write_text(key, encoding="utf-8")
    return key


def access_code() -> str:
    """工作台/只读入口的访问码（8 位大写字母数字，客服可手输）。首次自动生成。"""
    if ACCESS_CODE_PATH.exists():
        code = ACCESS_CODE_PATH.read_text(encoding="utf-8").strip()
        if code:
            return code
    code = "".join(secrets.choice(_ALPHABET) for _ in range(_CODE_LEN))
    ACCESS_CODE_PATH.write_text(code, encoding="utf-8")
    return code


def verify(token: str | None) -> bool:
    """校验请求头 X-Bridge-Token / X-Read-Token（工作台访问码）。"""
    if not token:
        return False
    return hmac.compare_digest(token, access_code())


def key_fingerprint() -> str:
    """访问码指纹（前 8 位哈希，不是访问码本身，供人工核对两边配的是同一把）。"""
    return hashlib.sha256(access_code().encode()).hexdigest()[:8]
