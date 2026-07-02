"""rotate_master_key.py — 轮换 MASTER_KEY 并重新加密所有 Provider.api_key_encrypted

历史背景（独立审查标记的高危点修复配套）：
  Provider.api_key 现在用 MASTER_KEY 派生的 Fernet key 加密存储。
  运维场景：MASTER_KEY 可能因为员工离职 / 密钥泄漏 / 定期轮换而需要更换。
  本脚本：
    1. 用旧 MASTER_KEY 解密所有 Provider.api_key_encrypted
    2. 用新 MASTER_KEY 重新加密
    3. 写回 DB

用法:
    # 1. 生成新 MASTER_KEY（先保存到新位置）
    python -m scripts.generate_master_key

    # 2. 跑轮换：旧 MASTER_KEY 仍在 env，新 MASTER_KEY 通过 --new-key 传
    python -m scripts.rotate_master_key --new-key <new_master_key>

    # 3. 验证：把旧 MASTER_KEY 从 env 移除，新 MASTER_KEY 注入，重启后端
    #    跑 pytest 或访问 /providers 端点验证 api_key 仍能解密

注意事项：
  - 必须在后端**停机期间**运行（避免 in-flight read 拿旧 key、新 write 拿新 key 的竞态）
  - 运行前**先备份 DB**（cp data/novel_assistant.db data/novel_assistant.db.bak）
  - 如果新旧 MASTER_KEY 任一不对，脚本立刻退出不解密（fail-fast）
"""
from __future__ import annotations

import argparse
import base64
import sys
from pathlib import Path

# 让脚本能 import app.* (workdir 设到 backend/)
BACKEND_ROOT = Path(__file__).resolve().parents[2]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))


def _validate_key(key: str, name: str) -> bytes:
    """校验 key 是合法 base64-urlsafe 32 字节。"""
    try:
        decoded = base64.urlsafe_b64decode(key)
        if len(decoded) != 32:
            raise ValueError(f"base64 decode 后 {len(decoded)} 字节，应为 32")
        return key.encode("ascii")
    except Exception as e:
        print(f"✗ {name} 不合法：{e}", file=sys.stderr)
        sys.exit(1)


def main() -> int:
    parser = argparse.ArgumentParser(description="轮换 MASTER_KEY 并重加密所有 Provider.api_key")
    parser.add_argument(
        "--new-key",
        required=True,
        help="新的 MASTER_KEY（base64-urlsafe 44 字符，generate_master_key.py 输出）",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="只统计要轮换的 provider 数量，不真改 DB",
    )
    args = parser.parse_args()

    new_key = _validate_key(args.new_key, "new MASTER_KEY")

    # 旧 MASTER_KEY 必须仍在 env（security.get_master_key 读它解密）
    from app.security import get_master_key
    try:
        old_key = get_master_key()
    except Exception as e:
        print(f"✗ 读旧 MASTER_KEY 失败：{e}", file=sys.stderr)
        print("  请确保环境变量 MASTER_KEY 仍是旧 key，再重跑", file=sys.stderr)
        return 1

    if old_key == new_key:
        print("✗ 新旧 MASTER_KEY 相同，无需轮换", file=sys.stderr)
        return 1

    # 1. 列出所有有 api_key_encrypted 的 provider
    from app.database import SessionLocal
    from app.models import Provider
    from app.security import decrypt_api_key, encrypt_api_key

    db = SessionLocal()
    try:
        providers = db.query(Provider).filter(Provider.api_key_encrypted.isnot(None)).all()
        if not providers:
            print("✓ 无 provider 需要轮换（DB 里没有 api_key_encrypted）")
            return 0

        print(f"找到 {len(providers)} 个 provider 待轮换：")
        for p in providers:
            print(f"  - {p.id} ({p.name}) suffix=...{p.api_key_suffix}")

        if args.dry_run:
            print("\n[dry-run] 不实际改 DB")
            return 0

        # 2. 备份提示
        print("\n⚠️  请确认你已经备份 DB（cp data/novel_assistant.db data/novel_assistant.db.bak）")
        print("    然后在另一个终端: Ctrl-C 取消，或按 Enter 继续轮换")
        try:
            input("按 Enter 继续: ")
        except EOFError:
            pass  # 非交互式（CI / pipe）直接继续

        # 3. 用旧 key 解密 + 用新 key 加密 + 写回
        from cryptography.fernet import Fernet
        old_fernet = Fernet(old_key)
        new_fernet = Fernet(new_key)

        succeeded = 0
        failed = 0
        for p in providers:
            try:
                plain = old_fernet.decrypt(p.api_key_encrypted.encode("ascii")).decode("utf-8")
                new_cipher = new_fernet.encrypt(plain.encode("utf-8")).decode("ascii")
                # round-trip 验证：再用新 key 解密应得原文
                assert new_fernet.decrypt(new_cipher.encode("ascii")).decode("utf-8") == plain
                p.api_key_encrypted = new_cipher
                succeeded += 1
            except Exception as e:
                print(f"  ✗ {p.id} ({p.name}) 失败：{e}", file=sys.stderr)
                failed += 1

        db.commit()
        print(f"\n✓ 轮换完成: {succeeded} 成功, {failed} 失败")
        if failed > 0:
            print("  失败的 provider 没更新 — 可重跑本脚本（只重试失败项）")
            return 1

        print("\n下一步：")
        print("  1. 把新 MASTER_KEY 注入环境变量（K8s Secret / .env / 系统 env）")
        print("  2. 从环境变量移除旧 MASTER_KEY")
        print("  3. 重启后端进程")
        print("  4. 跑 pytest + 访问 /providers 验证解密仍正常")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())