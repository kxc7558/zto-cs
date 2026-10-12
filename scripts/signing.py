"""更新包签名（Ed25519）：开发者持私钥发布，客户端持公钥验签。

修复 review CRITICAL-2：未加密 HTTP 上的裸 SHA256 是校验和不是签名——
MITM 可以同时替换 manifest 和 payload。正确做法：
- 开发者本机持私钥（~/.zto-cs-signing.key，绝不进 git、绝不发给客服）
- 公钥硬编码进客户端代码（update_pubkey.py），验签在客户端
- HTTP 明文不影响安全性：改一个字节，验签必失败

零依赖生成（标准库无 Ed25519，用 pynacl；客户端也依赖 pynacl——安装.bat 已装）。
"""
import hashlib
from pathlib import Path

from nacl.signing import SigningKey, VerifyKey
from nacl.encoding import HexEncoder

PRIVATE_KEY_PATH = Path.home() / ".zto-cs-signing.key"   # 开发者本机，不进 git
PUBKEY_PATH = Path.home() / ".zto-cs-signing.pub"


def generate_keypair() -> tuple[str, str]:
    """首次生成密钥对。返回 (私钥hex, 公钥hex)。"""
    sk = SigningKey.generate()
    priv = sk.encode().hex()
    pub = sk.verify_key.encode().hex()
    PRIVATE_KEY_PATH.write_text(priv, encoding="utf-8")
    PUBKEY_PATH.write_text(pub, encoding="utf-8")
    return priv, pub


def load_private() -> SigningKey:
    if not PRIVATE_KEY_PATH.exists():
        generate_keypair()
    return SigningKey(PRIVATE_KEY_PATH.read_text(encoding="utf-8").strip(), encoder=HexEncoder)


def sign_payload(payload: bytes) -> dict:
    """签名一个更新包。返回 {signature, sha256}。"""
    sk = load_private()
    sig = sk.sign(payload).signature.hex()
    return {"signature": sig, "sha256": hashlib.sha256(payload).hexdigest()}
