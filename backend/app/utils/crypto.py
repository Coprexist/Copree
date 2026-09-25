"""
加密工具模块
使用 cryptography.fernet 加密敏感值（用户 API Key、插件配置里的机密项）

对外语义只有两个：encrypt_secret / decrypt_secret。
encrypt_api_key / decrypt_api_key 保留为同义别名——调用点很多，
但实现只有一份，将来换算法只改这里。
"""
import logging
from cryptography.fernet import Fernet, InvalidToken
import base64
import hashlib
from app.config import settings

logger = logging.getLogger(__name__)


class APIKeyDecryptError(ValueError):
    """解密失败（密钥不匹配或数据损坏）"""
    pass


def _get_fernet() -> Fernet:
    """从配置密钥生成 Fernet 实例"""
    # Fernet 要求 32 字节的 base64 编码密钥
    key = hashlib.sha256(settings.encryption_key.encode()).digest()
    fernet_key = base64.urlsafe_b64encode(key)
    return Fernet(fernet_key)


def encrypt_secret(value: str) -> str:
    """加密一个敏感字符串"""
    return _get_fernet().encrypt(value.encode()).decode()


def decrypt_secret(encrypted: str) -> str:
    """解密一个敏感字符串；密钥不匹配或数据损坏时抛 APIKeyDecryptError"""
    try:
        return _get_fernet().decrypt(encrypted.encode()).decode()
    except InvalidToken:
        raise APIKeyDecryptError("解密失败：密钥不匹配或数据已损坏，请重新填写")


# 历史别名（同义，不是第二份实现）
encrypt_api_key = encrypt_secret
decrypt_api_key = decrypt_secret
