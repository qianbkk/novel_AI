"""app/security.py — Provider API key 加密

历史背景（独立审查标记的高危点）：
  Provider.api_key 之前是 Column(String, nullable=False) 明文存 SQLite。
  数据库文件泄漏 = 全部供应商 key 直接曝光。
  部署前必修，不是"以后再说"。

加密方案：
  - cryptography.fernet (AES128-CBC + HMAC) — 业界标准对称加密
  - MASTER_KEY 通过 env 注入（base64 编码的 32 字节）
  - DB 里存 ciphertext（base64 字符串），读时解密
  - UI 显示用 _key_suffix 明文后 4 位（"sk-...xxxx" 形式）—— 方便用户认 key

启动行为：
  - MASTER_KEY env 已设置 → 用它
  - 没设置 → 临时生成一个 + log warning（让 dev 模式仍能跑，但生产必须设）
  - 持久化：生成时不写盘，重启会失效（生产部署必须设 MASTER_KEY）
"""
from __future__ import annotations

import base64
import os
import secrets
from typing import Optional

from .logging_setup import get_logger

log = get_logger("novel_ai.security")

# Fernet key 长度：base64(32 bytes) = 44 chars
_FERNET_KEY_LEN = 44
# 明文后缀显示位数（UI 用）
_KEY_SUFFIX_LEN = 4


def _generate_fernet_key() -> bytes:
    """生成一个新的 Fernet key（base64(32 random bytes)）。"""
    return base64.urlsafe_b64encode(secrets.token_bytes(32))


def get_master_key() -> bytes:
    """从 env 读 MASTER_KEY，没设就生成一个 + 警告。

    Returns:
        32-byte Fernet key（base64 编码后是 44 字节字符串）

    Raises:
        RuntimeError: env 里的 MASTER_KEY 格式不对
    """
    env_key = os.environ.get("MASTER_KEY", "").strip()
    if env_key:
        # 校验格式
        try:
            decoded = base64.urlsafe_b64decode(env_key)
            if len(decoded) != 32:
                raise RuntimeError(
                    f"MASTER_KEY 长度不对（base64 decode 后 {len(decoded)} 字节，应为 32）"
                )
            return env_key.encode("ascii")
        except Exception as e:
            if isinstance(e, RuntimeError):
                raise
            raise RuntimeError(f"MASTER_KEY 不是有效的 base64 编码：{e}") from e

    # 没设 → 生成临时 key + 警告
    log.warning(
        "MASTER_KEY 环境变量未设置，已临时生成一个 Fernet key。\n"
        "  ⚠️  重启进程后这个 key 会变，已加密的 key 无法解密！\n"
        "  生产部署务必设置 MASTER_KEY（生成: python -c \"import base64,secrets; print(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())\"）"
    )
    return _generate_fernet_key()


def _fernet():
    """构造 Fernet 实例（懒加载，确保每次都拿到当前 MASTER_KEY）。"""
    from cryptography.fernet import Fernet
    return Fernet(get_master_key())


def encrypt_api_key(plaintext: str) -> str:
    """加密明文 API key，返回 base64 ciphertext 字符串。

    Args:
        plaintext: 明文 API key（不能为空）

    Returns:
        base64-encoded Fernet token（DB 存这个）
    """
    if not plaintext:
        raise ValueError("api_key 明文不能为空")
    token = _fernet().encrypt(plaintext.encode("utf-8"))
    return token.decode("ascii")


def decrypt_api_key(ciphertext: str) -> str:
    """解密 API key。

    Args:
        ciphertext: DB 里存的 base64 ciphertext

    Returns:
        明文 API key

    Raises:
        ValueError: ciphertext 为空或解密失败
    """
    if not ciphertext:
        raise ValueError("api_key ciphertext 不能为空")
    try:
        plain = _fernet().decrypt(ciphertext.encode("ascii"))
        return plain.decode("utf-8")
    except Exception as e:
        raise ValueError(
            f"api_key 解密失败（可能是 MASTER_KEY 变了或数据被损坏）: {e}"
        ) from e


def key_suffix(plaintext: str) -> str:
    """返回明文后 4 位（UI 显示用，"sk-...abcd"）。

    注意：明文只在写入瞬间使用，DB 里不存明文也不存完整后缀。
    """
    if not plaintext:
        return ""
    return plaintext[-_KEY_SUFFIX_LEN:]