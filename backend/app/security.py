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
  - 没设置 → 临时生成一个（进程级缓存）+ log warning（让 dev 模式仍能跑，但生产必须设）
  - 持久化：生成时不写盘，重启会失效（生产部署必须设 MASTER_KEY）

迭代 #72：修复 in-process key 漂移 bug
  之前每次调 get_master_key() 都会重新生成随机 key（当 MASTER_KEY 未设时），
  导致 encrypt_api_key() → decrypt_api_key() 同进程内立刻解密失败——
  文档承诺"至少同进程内稳定"是假的。
  修法：dev 模式首次生成后缓存到模块级 _dev_master_key；
  env 路径仍是 source-of-truth（不缓存，让测试 monkeypatch 能即时生效）。
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

# 迭代 #72: dev 模式（未设 MASTER_KEY）首次生成的 key 缓存到这里，
# 保证同进程内 encrypt → decrypt 一致。重启进程会丢（dev 模式预期行为）。
# env 路径不走这个缓存（每次重新读 env，让测试 / 配置变更能即时生效）。
_dev_master_key: Optional[bytes] = None


def _generate_fernet_key() -> bytes:
    """生成一个新的 Fernet key（base64(32 random bytes)）。"""
    return base64.urlsafe_b64encode(secrets.token_bytes(32))


def get_master_key() -> bytes:
    """从 env 读 MASTER_KEY，没设就生成一个 + 缓存 + 警告。

    Returns:
        32-byte Fernet key（base64 编码后是 44 字节字符串）

    Raises:
        RuntimeError: env 里的 MASTER_KEY 格式不对

    迭代 #72：dev 模式缓存到 _dev_master_key，同进程多次调用返回同一个 key，
    否则 encrypt_api_key 跟 decrypt_api_key 在同一进程里拿到的 key 不同，
    文档承诺的"dev 模式不设 MASTER_KEY 也能跑"才能成立。
    """
    global _dev_master_key
    env_key = os.environ.get("MASTER_KEY", "").strip()
    if env_key:
        # env 路径：每次都重新读（source of truth）—— 让测试 monkeypatch
        # 设 MASTER_KEY 后立刻生效；同时也保证生产配置变更无需重启 cache
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

    # 没设 → 复用首次生成的 key（迭代 #72：避免每次调用都生新 key）
    if _dev_master_key is None:
        _dev_master_key = _generate_fernet_key()
        log.warning(
            "MASTER_KEY 环境变量未设置，已临时生成一个 Fernet key（本次进程内复用）。\n"
            "  ⚠️  重启进程后这个 key 会变，已加密的 key 无法解密！\n"
            "  生产部署务必设置 MASTER_KEY（生成: python -c \"import base64,secrets; print(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())\"）"
        )
    return _dev_master_key


def reset_master_key_cache() -> None:
    """重置 dev 模式缓存的 master key（用于测试，或运维强制重新生成）。

    不会清除已经写入 DB 的 ciphertext——之前的密文用下一个 key 仍然解不开。
    仅在 dev 模式（无 env）有意义。
    """
    global _dev_master_key
    _dev_master_key = None


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