"""app/security.py — Provider API key 加密

历史背景（独立审查标记的高危点）：
  Provider.api_key 之前是 Column(String, nullable=False) 明文存 SQLite。
  数据库文件泄漏 = 全部供应商 key 直接曝光。
  部署前必修，不是"以后再说"。

加密方案：
  - cryptography.fernet (AES128-CBC + HMAC) — 业界标准对称加密
  - MASTER_KEY 通过 env 注入（base64 编码的 32 字节）；dev 模式 fallback
    到持久化文件 backend/data/.dev_master_key（迭代 #82，见下）
  - DB 里存 ciphertext（base64 字符串），读时解密
  - UI 显示用 _key_suffix 明文后 4 位（"sk-...xxxx" 形式）—— 方便用户认 key

启动行为（优先级从高到低）：
  1) MASTER_KEY env 已设置 → 用它（source-of-truth，覆盖一切）
  2) MASTER_KEY env 未设置 → 读 backend/data/.dev_master_key（gitignored）
     - 文件存在且有效 → 用它（dev 模式重启 / --reload 安全）
     - 文件不存在 → 临时生成一个新的，写到磁盘 + log warning
  3) 生产环境强制 NOVEL_PRODUCTION=1 必须设 env MASTER_KEY（main.py 检查）

迭代 #72 修复 in-process key 漂移 bug（dev 模式同进程 encrypt→decrypt 必须一致）
迭代 #82 修复 dev 模式 --reload 安全：
  之前：dev 模式只在 _dev_master_key 模块级缓存，uvicorn --reload 重启后
    模块状态清空 → 每次 reload 都生成新 key → 已加密的 Provider key 全部
    永久失效（"昨天还好好的今天所有 Provider 突然调用失败"）。
  现在：dev 模式首次生成后写到 backend/data/.dev_master_key（gitignored），
    后续启动（包括 --reload 自动重启）自动读回同个 key。env MASTER_KEY
    仍是 source-of-truth（运维改了 env 自动覆盖）。
  生产部署务必设 MASTER_KEY（推荐用 scripts/generate_master_key.py）。
"""
from __future__ import annotations

import base64
import os
import secrets
from pathlib import Path
from typing import Optional

from .logging_setup import get_logger

log = get_logger("novel_ai.security")

# Fernet key 长度：base64(32 bytes) = 44 chars
_FERNET_KEY_LEN = 44
# 明文后缀显示位数（UI 用）
_KEY_SUFFIX_LEN = 4

# 迭代 #82: dev 模式持久化 master key 的路径（gitignored）
# 用户反馈 (#82 task): dev.bat --reload 工作流下 #72 模块级缓存完全无效
# （uvicorn 重启清空所有模块状态），必须持久化到磁盘。
# 文件权限: 仅 dev 模式使用（生产部署被 _check_master_key_in_production 拦截），
# 所以 0600 在 Windows 上不可强求——让文件系统默认权限即可。
_DEV_MASTER_KEY_PATH = Path(__file__).resolve().parent.parent / "data" / ".dev_master_key"

# 迭代 #72: dev 模式（未设 MASTER_KEY env）首次生成的 key 内存缓存。
# 进程内复用，避免同进程内 encrypt/decrypt 用不同 key。
_dev_master_key: Optional[bytes] = None


def _generate_fernet_key() -> bytes:
    """生成一个新的 Fernet key（base64(32 random bytes)）。"""
    return base64.urlsafe_b64encode(secrets.token_bytes(32))


def _validate_fernet_key(candidate: bytes) -> bytes:
    """校验候选 key 格式有效（base64(32 bytes)）；格式错返回 None 不抛。"""
    try:
        # Fernet 期望 base64(32 bytes) 字符串
        decoded = base64.urlsafe_b64decode(candidate)
        if len(decoded) == 32:
            return candidate
    except Exception:
        pass
    return b""  # type: ignore[return-value]


def _load_persisted_dev_key() -> Optional[bytes]:
    """从 _DEV_MASTER_KEY_PATH 读 dev master key。无效或不存在 → None。"""
    try:
        if not _DEV_MASTER_KEY_PATH.exists():
            return None
        raw = _DEV_MASTER_KEY_PATH.read_text(encoding="utf-8").strip()
        if not raw:
            return None
        candidate = raw.encode("ascii")
        validated = _validate_fernet_key(candidate)
        return validated if validated else None
    except Exception as e:
        log.warning("读取 %s 失败（%s），将重新生成 dev key",
                    _DEV_MASTER_KEY_PATH, e)
        return None


def _persist_dev_key(key: bytes) -> None:
    """把 dev master key 写到磁盘（atomic write 避免半写）。"""
    try:
        _DEV_MASTER_KEY_PATH.parent.mkdir(parents=True, exist_ok=True)
        # 写到 .tmp 后原子 rename——避免 reload 中文件被半写
        tmp = _DEV_MASTER_KEY_PATH.with_suffix(_DEV_MASTER_KEY_PATH.suffix + ".tmp")
        tmp.write_text(key.decode("ascii"), encoding="utf-8")
        os.replace(tmp, _DEV_MASTER_KEY_PATH)
        log.info("dev master key 已持久化到 %s（gitignored），自动 reload 安全",
                 _DEV_MASTER_KEY_PATH)
    except Exception as e:
        log.warning("持久化 dev master key 失败（%s），下次启动 key 会变",
                    e)


def get_master_key() -> bytes:
    """获取 MASTER_KEY 用于 Fernet 加密。

    解析顺序：
      1) MASTER_KEY env 已设置 → 用它（source-of-truth）
      2) backend/data/.dev_master_key 文件存在且有效 → 用它（迭代 #82）
      3) 上面都没有 → 临时生成，写到磁盘 + log warning

    Returns:
        32-byte Fernet key（base64 编码后是 44 字节字符串）

    Raises:
        RuntimeError: env 里 MASTER_KEY 格式不对
    """
    global _dev_master_key
    env_key = os.environ.get("MASTER_KEY", "").strip()
    if env_key:
        # 1) env 路径：每次都重新读（source of truth），让 monkeypatch / 配置变更立刻生效
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

    # 2) 没有 env → 先复用进程级缓存（性能 + 同进程一致性）
    if _dev_master_key is not None:
        return _dev_master_key

    # 3) 没有缓存 → 尝试从磁盘加载（dev 模式 --reload 安全）
    persisted = _load_persisted_dev_key()
    if persisted is not None:
        _dev_master_key = persisted
        log.info(
            "dev mode: 使用持久化的 master key（%s）。"
            "dev.bat --reload / 重启进程仍然能解密已存的 Provider key。",
            _DEV_MASTER_KEY_PATH,
        )
        return _dev_master_key

    # 4) 都没有 → 临时生成 + 持久化到磁盘 + warning
    new_key = _generate_fernet_key()
    _dev_master_key = new_key
    log.warning(
        "MASTER_KEY 环境变量未设置，dev 模式自动生成并持久化一个新 Fernet key 到 %s。\n"
        "  ✅ 持久化后 dev.bat --reload / 重启进程仍能解密已存的 Provider key。\n"
        "  ⚠️  生产部署务必设置 MASTER_KEY env（推荐: export MASTER_KEY=\"$(python -m scripts.generate_master_key --print)\"）"
    )
    _persist_dev_key(new_key)
    return new_key


def reset_master_key_cache() -> None:
    """重置 dev 模式缓存的 master key + 删除持久化文件（用于测试或运维强制重置）。

    不会清除已经写入 DB 的 ciphertext——之前的密文用下一个 key 仍然解不开。
    仅在 dev 模式（无 env）有意义。生产模式下不做任何事（避免意外删 env）。
    """
    global _dev_master_key
    _dev_master_key = None
    try:
        if _DEV_MASTER_KEY_PATH.exists():
            _DEV_MASTER_KEY_PATH.unlink()
            log.info("dev master key 文件已删除：%s", _DEV_MASTER_KEY_PATH)
    except Exception as e:
        log.warning("删除 %s 失败（%s）", _DEV_MASTER_KEY_PATH, e)


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