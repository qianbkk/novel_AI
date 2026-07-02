"""generate_master_key.py — 生成 Fernet MASTER_KEY（用于 Provider API key 加密）

用法:
    python -m scripts.generate_master_key

输出:
    MASTER_KEY=<base64-urlsafe-44-chars>

历史背景（独立审查标记的高危点修复）：
  Provider.api_key 之前明文存 SQLite，DB 泄漏 = 全部供应商 key 曝光。
  本脚本生成的对称密钥用于加密存储 api_key。

部署步骤：
  1. python -m scripts.generate_master_key
  2. 把输出的 MASTER_KEY 写进部署环境（K8s Secret / .env / 系统环境变量）
  3. 启动后端时确保该 env 注入到 uvicorn worker 进程

注意事项：
  - 不要把 MASTER_KEY commit 到仓库（虽然不是直接泄漏 api_key，
    但持有 MASTER_KEY 的人能解密所有 Provider api_key）
  - 轮换 MASTER_KEY 需要重新加密所有 Provider.api_key_encrypted 字段
    （本脚本不处理 — 留作未来 migration 工具）
  - 多 worker / 多机部署必须所有进程用同一个 MASTER_KEY
"""
from __future__ import annotations

import base64
import secrets
import sys


def generate() -> str:
    """生成一个新的 Fernet key（base64-urlsafe 编码 32 字节随机数）。"""
    return base64.urlsafe_b64encode(secrets.token_bytes(32)).decode("ascii")


def main() -> int:
    key = generate()
    # 校验生成的 key 真的能构造 Fernet（round-trip）
    try:
        from cryptography.fernet import Fernet
        f = Fernet(key.encode("ascii"))
        test_token = f.encrypt(b"sanity-check")
        assert f.decrypt(test_token) == b"sanity-check"
    except Exception as e:
        print(f"✗ 生成失败：{e}", file=sys.stderr)
        return 1

    print("# 新生成的 MASTER_KEY（设置到环境变量）：")
    print(f"MASTER_KEY={key}")
    print()
    print("# 校验：Fernet 构造 + encrypt/decrypt round-trip 成功")
    print("# 长度：", len(key), "字符（应为 44）")
    return 0


if __name__ == "__main__":
    sys.exit(main())