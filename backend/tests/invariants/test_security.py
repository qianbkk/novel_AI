"""security/ — Phase 3 测试拆分

不变量测试按业务域分文件存放。
测试按业务域直接收集，不再经过兼容 re-export 模块。
"""

from tests._paths import REPO_ROOT, BACKEND_ROOT
import json
import sys
from pathlib import Path
import pytest

BACKEND = Path(REPO_ROOT)
sys.path.insert(0, str(BACKEND))

# 共享 schema validator imports
from app.schema_validator import (  # noqa: E402,F401
    validate_setting_package, validate_chapter_meta, SchemaError,
    get_setting_package_schema, get_chapter_meta_schema,
    validate_world_view_rich, validate_character_card, validate_entity_relation_rich,
    get_world_view_rich_schema, get_character_card_schema, get_entity_relation_rich_schema,
)

class TestMasterKeyRotation:
    """历史背景（独立审查标记的高危点修复配套）：
      Provider.api_key 用 MASTER_KEY 派生的 Fernet 加密。
      运维场景：MASTER_KEY 可能因为员工离职 / 定期轮换需要更换。
      必须有工具支持轮换（避免手动 SQL 解密重加密出错）。

      本轮新增：scripts/rotate_master_key.py
        - 旧 MASTER_KEY 仍在 env
        - 新 MASTER_KEY 通过 --new-key 传入
        - 自动列出待轮换 provider，支持 --dry-run
        - round-trip 校验每个 provider 解密+再加密成功才 commit
    """

    def test_rotate_master_key_script_exists(self):
        """rotate_master_key.py 必须存在 + 含 main() + 关键选项。"""
        from pathlib import Path
        script = Path(REPO_ROOT) / "backend" / "scripts" / "rotate_master_key.py"
        assert script.exists(), "backend/scripts/rotate_master_key.py 不存在"
        # 验证含 main + --new-key + --dry-run
        content = script.read_text(encoding="utf-8")
        assert "def main()" in content, "rotate_master_key.py 必须定义 main()"
        assert '"--new-key"' in content or "'--new-key'" in content, (
            "rotate_master_key.py 必须有 --new-key 参数"
        )
        assert '"--dry-run"' in content or "'--dry-run'" in content, (
            "rotate_master_key.py 必须有 --dry-run 参数"
        )
        # 关键安全特性：fail-fast on invalid key
        assert "validate" in content.lower() or "_validate_key" in content, (
            "rotate_master_key.py 必须校验 key 合法性（fail-fast）"
        )

    def test_rotate_script_validates_new_key(self):
        """传非法 --new-key 必须立刻报错退出（不开始改 DB）。"""
        import subprocess
        from pathlib import Path
        script_dir = Path(REPO_ROOT) / "backend" / "scripts"
        # 完全非 base64
        result = subprocess.run(
            ["python", "-m", "scripts.rotate_master_key", "--new-key", "not-base64-at-all!!!"],
            cwd=script_dir.parent,
            capture_output=True, text=True, timeout=10,
        )
        assert result.returncode != 0, (
            "非法 --new-key 应该失败但成功 — 可能没校验"
        )
        assert "不合法" in result.stderr or "Invalid" in result.stderr, (
            f"错误信息应明确说 key 不合法，实际 stderr: {result.stderr!r}"
        )


class TestProviderApiKeyEncrypted:
    """历史背景（独立审查标记的高危点）：
      Provider.api_key 之前是 Column(String, nullable=False) 明文存 SQLite。
      数据库文件泄漏 = 全部供应商 key 直接曝光。
      部署前必修，不是"以后再说"。

    本轮修复：
      - 新字段 api_key_encrypted（Fernet ciphertext）+ api_key_suffix（明文后 4 位）
      - providers.py 写时 encrypt_api_key，读时通过 ProviderOut 不暴露明文
      - 前端只看到 api_key_set=true + api_key_suffix="xxxx"
    """

    def test_provider_model_has_no_plaintext_api_key_column(self):
        """Provider model 必须没有 api_key 明文字段（已被 api_key_encrypted 替换）。"""
        from app.models import Provider
        columns = {c.name for c in Provider.__table__.columns}
        assert "api_key" not in columns, (
            "Provider model 还保留明文 api_key 列 — 高危！数据库泄漏 = 全部 key 曝光"
        )
        assert "api_key_encrypted" in columns, (
            "Provider model 缺 api_key_encrypted 列（应存 Fernet ciphertext）"
        )
        assert "api_key_suffix" in columns, (
            "Provider model 缺 api_key_suffix 列（UI 显示用后 4 位）"
        )

    def test_provider_out_does_not_expose_plaintext_key(self):
        """ProviderOut schema 不能有 api_key 明文字段。"""
        from app.schemas import ProviderOut
        fields = ProviderOut.model_fields
        assert "api_key" not in fields, (
            "ProviderOut schema 不能有 api_key 明文字段（会泄漏到前端）"
        )
        assert "api_key_suffix" in fields, (
            "ProviderOut schema 缺 api_key_suffix（前端无法显示后 4 位）"
        )
        assert "api_key_set" in fields, (
            "ProviderOut schema 缺 api_key_set（前端无法判断是否已配置）"
        )

    def test_encrypt_decrypt_roundtrip(self):
        """encrypt → decrypt 必须能还原明文。"""
        from app.security import encrypt_api_key, decrypt_api_key, get_master_key
        import os, base64, secrets
        # 测试用稳定 key（避免 get_master_key 拿到临时 key）
        os.environ["MASTER_KEY"] = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode()
        plain = "sk-test-1234567890abcdef"
        encrypted = encrypt_api_key(plain)
        assert encrypted != plain, "ciphertext 必须 != 明文"
        assert decrypt_api_key(encrypted) == plain, "decrypt 必须还原明文"

    def test_ciphertext_not_equal_plaintext(self):
        """两次加密同一明文 → ciphertext 必须不同（Fernet 每次随机 IV）。"""
        import os, base64, secrets
        os.environ["MASTER_KEY"] = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode()
        from app.security import encrypt_api_key
        plain = "sk-test-fixed-plaintext"
        c1 = encrypt_api_key(plain)
        c2 = encrypt_api_key(plain)
        assert c1 != c2, "两次同明文必须出不同 ciphertext（防止重放攻击）"

    def test_api_key_suffix_returns_last_4(self):
        """key_suffix 返回明文后 4 位（UI 显示用）。"""
        from app.security import key_suffix
        assert key_suffix("sk-test-1234567890abcdef") == "cdef"
        assert key_suffix("") == ""

    def test_create_provider_does_not_store_plaintext(self, monkeypatch):
        """create_provider API 调用后，DB 里必须没有明文 api_key。"""
        import os, base64, secrets
        monkeypatch.setenv("MASTER_KEY", base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())
        from app.database import SessionLocal
        from app.models import Provider
        from app.api.providers import create_provider
        from app.schemas import ProviderCreate

        plain_key = "sk-test-must-not-be-stored-plaintext-12345"
        db = SessionLocal()
        try:
            payload = ProviderCreate(
                name="test-encryption",
                provider_type="anthropic",
                api_key=plain_key,
                default_model="claude-test",
            )
            out = create_provider(payload, db)  # 返回 ProviderOut
            # 1. out 不能含 api_key 明文字段
            out_dict = out.model_dump()
            assert "api_key" not in out_dict, (
                "ProviderOut 响应包含 api_key 明文字段 — 高危！"
            )
            assert out_dict["api_key_set"] is True
            assert out_dict["api_key_suffix"] == plain_key[-4:], (
                f"api_key_suffix 应为明文后 4 位 {plain_key[-4:]!r}，"
                f"实际 {out_dict['api_key_suffix']!r}"
            )
            test_id = out.id
            # 2. 直接查 DB（绕过 pydantic）确认存的是 ciphertext
            row = db.get(Provider, test_id)
            assert plain_key not in (row.api_key_encrypted or ""), (
                "DB api_key_encrypted 字段包含明文 — 高危！"
            )
            assert row.api_key_encrypted.startswith("gAAAAA"), (
                f"api_key_encrypted 应为 Fernet ciphertext（gAAAAA 开头），"
                f"实际 {row.api_key_encrypted[:20]!r}"
            )
        finally:
            if 'test_id' in locals():
                p = db.get(Provider, test_id)
                if p:
                    db.delete(p)
                    db.commit()
            db.close()

    def test_provider_out_response_no_plaintext(self, monkeypatch):
        """API 返回的 ProviderOut 不能包含明文 api_key。"""
        import os, base64, secrets
        monkeypatch.setenv("MASTER_KEY", base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())
        from app.database import SessionLocal
        from app.models import Provider
        from app.api.providers import create_provider, _to_out
        from app.schemas import ProviderCreate

        plain_key = "sk-leak-test-secret-key-9999"
        db = SessionLocal()
        try:
            payload = ProviderCreate(
                name="test-leak",
                provider_type="anthropic",
                api_key=plain_key,
                default_model="claude-test",
            )
            provider = create_provider(payload, db)  # 返回 ProviderOut
            # _to_out 既支持 ORM 也支持 ProviderOut
            out = _to_out(provider)
            out_dict = out.model_dump()
            assert "api_key" not in out_dict, (
                "ProviderOut 响应包含 api_key 明文字段 — 高危！"
            )
            assert out_dict["api_key_set"] is True
            assert out_dict["api_key_suffix"] == plain_key[-4:]
            # 完整明文也不能出现在任何字段值里
            for k, v in out_dict.items():
                if isinstance(v, str):
                    assert plain_key not in v, (
                        f"明文 api_key 泄漏到 {k!r} 字段值：{v!r}"
                    )
            test_id = out.id
        finally:
            if 'test_id' in locals():
                p = db.get(Provider, test_id)
                if p:
                    db.delete(p)
                    db.commit()
            db.close()


class TestMasterKeyScriptsEndToEnd:
    """迭代 #12：脚本不是只 import — 必须能跑通真实 encrypt/decrypt。

    历史背景：
      generate_master_key.py 之前只测 import / round-trip sanity check，
      没测"用生成的 key 真能 encrypt + decrypt 跨模块"的真实场景。

    本测试验证：
      - generate_master_key.py 输出 44 字符 base64-urlsafe
      - 用生成的 key encrypt 一个 string + 用同一个 Fernet 实例
        decrypt 回原文
      - security.encrypt/decrypt 真读 MASTER_KEY env
    """

    def test_generated_key_can_encrypt_decrypt_roundtrip(self):
        """generate_master_key.py 输出的 key 真能用于 Fernet encrypt/decrypt。"""
        from cryptography.fernet import Fernet
        from pathlib import Path
        import subprocess
        backend_root = Path(BACKEND_ROOT)

        result = subprocess.run(
            ["python", "-m", "scripts.generate_master_key"],
            cwd=backend_root,
            capture_output=True, text=True, timeout=10,
        )
        assert result.returncode == 0, f"脚本失败：{result.stderr}"

        key_str = None
        for line in result.stdout.splitlines():
            if line.startswith("MASTER_KEY="):
                key_str = line.split("=", 1)[1].strip()
                break
        assert key_str is not None, f"脚本输出里没找到 MASTER_KEY=：{result.stdout!r}"
        assert len(key_str) == 44, f"MASTER_KEY 长度 {len(key_str)} ≠ 44"

        f = Fernet(key_str.encode("ascii"))
        plaintext = "sk-test-real-encryption-12345"
        ciphertext = f.encrypt(plaintext.encode("utf-8"))
        decrypted = f.decrypt(ciphertext).decode("utf-8")
        assert decrypted == plaintext, (
            f"round-trip 失败：plaintext={plaintext!r}, decrypted={decrypted!r}"
        )

    def test_two_consecutive_keys_are_different(self):
        """连续两次运行 generate 必产生不同 key（secrets 随机）。"""
        from pathlib import Path
        import subprocess
        backend_root = Path(BACKEND_ROOT)
        keys = []
        for _ in range(2):
            result = subprocess.run(
                ["python", "-m", "scripts.generate_master_key"],
                cwd=backend_root,
                capture_output=True, text=True, timeout=10,
            )
            for line in result.stdout.splitlines():
                if line.startswith("MASTER_KEY="):
                    keys.append(line.split("=", 1)[1].strip())
        assert len(keys) == 2, f"应拿到 2 个 key，实际 {keys}"
        assert keys[0] != keys[1], (
            f"连续两次 generate 应产生不同 key（secrets 随机），实际都 = {keys[0]}"
        )

    def test_security_encrypt_decrypt_uses_master_key_env(self, monkeypatch):
        """security.encrypt_api_key / decrypt_api_key 真的读 MASTER_KEY env。"""
        import os, base64, secrets
        test_key = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode()
        monkeypatch.setenv("MASTER_KEY", test_key)

        from app.security import encrypt_api_key, decrypt_api_key
        plain = "sk-test-secret-9999"
        ciphertext = encrypt_api_key(plain)
        assert ciphertext != plain
        # 同一 env 下解密必须成功
        assert decrypt_api_key(ciphertext) == plain

        # 改 env 模拟 MASTER_KEY 重置 / 错配 → 解密失败
        monkeypatch.setenv("MASTER_KEY", base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())
        import pytest
        with pytest.raises(ValueError, match="api_key 解密失败"):
            decrypt_api_key(ciphertext)


class TestRotateMasterKeyEndToEnd:
    """迭代 #13：rotate_master_key 真实轮换流程测试。

    之前只测 fail-fast on invalid new key，没测：
      - 旧 key encrypt 的数据 → 新 key re-encrypt
      - round-trip：拿新 key 解密应能恢复明文
      - 多个 provider 同时轮换

    注意：autouse fixture 在每个测试前清空 DB 里所有 test- 前缀的 provider，
    防止 invariant test 历史遗留的 provider（用不同 MASTER_KEY 加密）
    干扰 rotation 流程。
    """

    @pytest.fixture(autouse=True)
    def cleanup_test_providers(self):
        """每个 rotation 测试前清空 test- 前缀的 provider。"""
        from app.database import SessionLocal
        from app.models import Provider
        db = SessionLocal()
        try:
            for p in db.query(Provider).filter(Provider.id.like("test-%")).all():
                db.delete(p)
            db.commit()
        finally:
            db.close()
        yield  # 测试运行
        # teardown：测试结束也清理（避免污染后续测试）
        db = SessionLocal()
        try:
            for p in db.query(Provider).filter(Provider.id.like("test-%")).all():
                db.delete(p)
            db.commit()
        finally:
            db.close()

    def _make_provider(self, plain_key: str) -> str:
        """helper：插一个带 api_key_encrypted 的 provider，返回 id。"""
        from app.database import SessionLocal
        from app.models import Provider
        from app.security import encrypt_api_key, key_suffix
        import secrets
        db = SessionLocal()
        try:
            p = Provider(
                id=f"test-rotate-{secrets.token_hex(4)}",
                name=f"test-{secrets.token_hex(4)}",
                provider_type="anthropic",
                api_key_encrypted=encrypt_api_key(plain_key),
                api_key_suffix=key_suffix(plain_key),
                default_model="test",
            )
            db.add(p)
            db.commit()
            return p.id
        finally:
            db.close()

    def _cleanup_provider(self, provider_id: str):
        from app.database import SessionLocal
        from app.models import Provider
        db = SessionLocal()
        try:
            p = db.get(Provider, provider_id)
            if p:
                db.delete(p)
                db.commit()
        finally:
            db.close()

    def test_rotate_single_provider_end_to_end(self, monkeypatch):
        """旧 MASTER_KEY 加密的 Provider → rotate 后用新 key 仍能 decrypt。"""
        import os, base64, secrets
        from app.security import decrypt_api_key
        from pathlib import Path
        import importlib.util

        # 1. 设旧 MASTER_KEY（脚本会读 os.environ 拿旧 key）
        old_key = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode()
        monkeypatch.setenv("MASTER_KEY", old_key)

        # 2. 插一条 Provider（旧 key 加密的）
        plain = "sk-real-plaintext-for-rotation"
        provider_id = self._make_provider(plain)
        try:
            # 3. 加载脚本 + 调 rotate 函数（不通过 subprocess，monkeypatch 才能控）
            backend_root = Path(BACKEND_ROOT)
            spec = importlib.util.spec_from_file_location(
                "rotate_under_test",
                backend_root / "scripts" / "rotate_master_key.py",
            )
            mod = importlib.util.module_from_spec(spec)
            # 不调 spec.loader.exec_module（会跑 main / argparse）
            # 直接 import 模块体
            import sys
            sys.modules["rotate_under_test"] = mod
            with open(backend_root / "scripts" / "rotate_master_key.py", encoding="utf-8") as f:
                code = f.read()
            exec(compile(code, str(backend_root / "scripts" / "rotate_master_key.py"), "exec"), mod.__dict__)

            # 4. 轮换
            new_key = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode()
            import sys as _sys, builtins
            _sys.argv = ["rotate", "--new-key", new_key]
            # 脚本要求"按 Enter 继续"确认备份，monkeypatch 让它自动继续
            monkeypatch.setattr(builtins, "input", lambda prompt="": "")
            rc = mod.main()
            assert rc == 0, f"rotate_master_key.main 返回 {rc}"

            # 5. 切到新 MASTER_KEY，解密必须能拿到原明文
            monkeypatch.setenv("MASTER_KEY", new_key)
            from app.database import SessionLocal
            from app.models import Provider
            db = SessionLocal()
            try:
                p = db.get(Provider, provider_id)
                assert p is not None
                decrypted = decrypt_api_key(p.api_key_encrypted)
                assert decrypted == plain, (
                    f"rotate 后解密应得原明文：got {decrypted!r}, expected {plain!r}"
                )
            finally:
                db.close()
        finally:
            self._cleanup_provider(provider_id)

    def test_rotate_dry_run_does_not_modify_db(self, monkeypatch):
        """--dry-run 模式：列出 provider 但不实际改 DB。"""
        import os, base64, secrets
        from app.security import decrypt_api_key
        from pathlib import Path
        import importlib.util

        old_key = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode()
        monkeypatch.setenv("MASTER_KEY", old_key)

        plain = "sk-dryrun-test"
        provider_id = self._make_provider(plain)
        try:
            backend_root = Path(BACKEND_ROOT)
            spec = importlib.util.spec_from_file_location(
                "rotate_dry",
                backend_root / "scripts" / "rotate_master_key.py",
            )
            mod = importlib.util.module_from_spec(spec)
            import sys
            sys.modules["rotate_dry"] = mod
            with open(backend_root / "scripts" / "rotate_master_key.py", encoding="utf-8") as f:
                code = f.read()
            exec(compile(code, str(backend_root / "scripts" / "rotate_master_key.py"), "exec"), mod.__dict__)

            new_key = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode()
            import sys as _sys
            _sys.argv = ["rotate", "--new-key", new_key, "--dry-run"]
            rc = mod.main()
            assert rc == 0

            # 验证 DB 没改：旧 key 仍能解密
            decrypted = decrypt_api_key(
                # 旧 key 还在 env，从 DB 拿密文
                __import__("app.database", fromlist=["SessionLocal"]).SessionLocal().__enter__().__class__
            ) if False else None  # 简化为直接 DB 读
            from app.database import SessionLocal
            from app.models import Provider
            db = SessionLocal()
            try:
                p = db.get(Provider, provider_id)
                # 旧 key 解密应成功（说明 DB 没改）
                decrypted = decrypt_api_key(p.api_key_encrypted)
                assert decrypted == plain, "dry-run 不应修改 DB"
            finally:
                db.close()
        finally:
            self._cleanup_provider(provider_id)


class TestSecurityConstants:
    """最后 #25：锁死 security.py 的安全相关常量。"""
    def test_key_suffix_length_is_4(self):
        from app.security import _KEY_SUFFIX_LEN
        assert _KEY_SUFFIX_LEN == 4, (
            f"_KEY_SUFFIX_LEN 应为 4，实际 {_KEY_SUFFIX_LEN}"
        )

    def test_generate_fernet_key_returns_32_bytes(self):
        from app.security import _generate_fernet_key
        import base64
        key = _generate_fernet_key()
        decoded = base64.urlsafe_b64decode(key)
        assert len(decoded) == 32, f"Fernet key 解码后 {len(decoded)} 字节，应为 32"

    def test_decrypt_empty_ciphertext_raises(self):
        from app.security import decrypt_api_key
        import pytest
        with pytest.raises(ValueError, match="api_key ciphertext 不能为空"):
            decrypt_api_key("")

    def test_encrypt_empty_plaintext_raises(self):
        from app.security import encrypt_api_key
        import pytest
        with pytest.raises(ValueError, match="api_key 明文不能为空"):
            encrypt_api_key("")


class TestProviderTableSchema:
    """最后 #27：锁死 Provider 表的关键字段（防止 schema drift）。"""
    def test_provider_has_no_plaintext_api_key_column(self):
        from app.models import Provider
        columns = {c.name for c in Provider.__table__.columns}
        assert "api_key" not in columns, (
            "Provider 表还有明文 api_key 列 — 高危！"
        )

    def test_provider_has_encrypted_and_suffix_columns(self):
        from app.models import Provider
        columns = {c.name for c in Provider.__table__.columns}
        assert "api_key_encrypted" in columns
        assert "api_key_suffix" in columns

    def test_provider_encrypted_column_type_is_text(self):
        from app.models import Provider
        from sqlalchemy import Text
        col = Provider.__table__.columns["api_key_encrypted"]
        assert isinstance(col.type, Text), (
            f"api_key_encrypted 应为 Text 类型，实际 {type(col.type).__name__}"
        )

    def test_provider_name_not_nullable(self):
        from app.models import Provider
        col = Provider.__table__.columns["name"]
        assert col.nullable is False, (
            f"Provider.name 应 NOT NULL，实际 nullable={col.nullable}"
        )


class TestMasterKeyStableAcrossCalls:
    """迭代 #72（severe bug fix）：

    之前 `get_master_key()` 每次调用在 dev 模式（无 MASTER_KEY env）都会
    生成新的随机 key，导致：
      encrypt_api_key('sk-xxx') → encrypt with key_K1
      decrypt_api_key(cipher) → decrypt with key_K2 ≠ K1
      → ValueError "MASTER_KEY 变了或数据被损坏"

    文档承诺"dev 模式不设 MASTER_KEY 也能跑（至少同进程内稳定）"完全不成立。
    测试代码已经知道这个不稳定性，专门
    注入稳定 key 绕过测试，而不是修根本行为。本次迭代加上根本修复。

    修复：
      security._dev_master_key 模块级缓存，dev 模式首次生成后复用。
      env 路径不走缓存（source-of-truth，每次读 env）。
    """
    def _clean_dev_state(self, monkeypatch):
        """清掉 MASTER_KEY env + 模块缓存，强制走到 dev 分支。"""
        monkeypatch.delenv("MASTER_KEY", raising=False)
        from app import security
        monkeypatch.setattr(security, "_dev_master_key", None)

    def test_dev_mode_key_is_stable_across_calls(self, monkeypatch):
        """Dev 模式（无 MASTER_KEY env）：同进程多次调 get_master_key 必须返回同一个 key。"""
        self._clean_dev_state(monkeypatch)
        from app.security import get_master_key
        k1 = get_master_key()
        k2 = get_master_key()
        k3 = get_master_key()
        assert k1 == k2 == k3, \
            f"dev 模式同进程必须复用同一 key：k1={k1[:8]}.. k2={k2[:8]}.. k3={k3[:8]}.."

    def test_dev_mode_encrypt_decrypt_roundtrip(self, monkeypatch):
        """#72 复现测试：dev 模式 encrypt → decrypt 必须成功（同进程）。

        这是审计报告里"严重（已复现）"那条用的脚本——直接跑通才算修好。
        之前这条会抛 "api_key 解密失败（可能是 MASTER_KEY 变了或数据被损坏）"。
        """
        self._clean_dev_state(monkeypatch)
        from app.security import encrypt_api_key, decrypt_api_key
        plaintext = "sk-test-my-real-api-key-12345"
        ciphertext = encrypt_api_key(plaintext)
        decrypted = decrypt_api_key(ciphertext)
        assert decrypted == plaintext, (
            f"dev 模式同进程 encrypt → decrypt 必须拿回原文："
            f"期望={plaintext}, 实际={decrypted}"
        )

    def test_env_master_key_is_source_of_truth(self, monkeypatch):
        """设了 MASTER_KEY env 时，每次调 get_master_key 都读 env（不走缓存）。

        测试通过 monkeypatch 切换 env 来模拟"运维中途改 MASTER_KEY"场景。
        生产部署理论上不会改，但开发期间切换方便。
        """
        import base64, secrets
        from app import security
        # 先清掉缓存 + 清掉 env，确保干净起点
        monkeypatch.setattr(security, "_dev_master_key", None)
        monkeypatch.delenv("MASTER_KEY", raising=False)

        key_a = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode()
        key_b = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode()
        assert key_a != key_b

        monkeypatch.setenv("MASTER_KEY", key_a)
        from app.security import get_master_key
        assert get_master_key().decode() == key_a
        assert get_master_key().decode() == key_a  # 第二次仍然读 env

        # 切换 env 应立刻生效（不走缓存）
        monkeypatch.setenv("MASTER_KEY", key_b)
        assert get_master_key().decode() == key_b, \
            "env 路径必须是 source-of-truth，切了 env 必须立刻用新 key"

    def test_reset_master_key_cache_helper(self, monkeypatch):
        """reset_master_key_cache() 后下次 generate 必须拿到新 key（dev 分支）。"""
        self._clean_dev_state(monkeypatch)
        from app.security import get_master_key, reset_master_key_cache
        k1 = get_master_key()
        reset_master_key_cache()
        k2 = get_master_key()
        assert k1 != k2, \
            f"reset 后必须重新生成：k1={k1[:8]}.. k2={k2[:8]}.."

    def test_source_has_module_level_cache(self):
        """源码扫描：security.py 必须有 _dev_master_key 模块级缓存（#72 标志）。"""
        import inspect
        from app import security
        src = inspect.getsource(security)
        assert "_dev_master_key" in src, \
            "app.security 必须有 _dev_master_key 模块级缓存变量（#72 in-process key 漂移修复）"
        # 同时 get_master_key 函数体里必须有"先查缓存再生成"的语义
        func_src = inspect.getsource(security.get_master_key)
        assert "if _dev_master_key is None" in func_src or \
               "if _dev_master_key is not None" in func_src, \
            "get_master_key 必须有 cache hit/miss 分支（#72）"

    def test_reset_master_key_cache_public_api(self):
        """reset_master_key_cache() 必须存在并是公开 API（运维 / 测试可调）。"""
        from app.security import reset_master_key_cache
        # 必须能调用且不抛
        reset_master_key_cache()
        # 必须能从同 module 导入（不是 _private）
        import app.security
        assert hasattr(app.security, "reset_master_key_cache"), \
            "app.security.reset_master_key_cache 必须存在"
        # 公开 API 的 signature 应该没参数
        import inspect
        sig = inspect.signature(reset_master_key_cache)
        assert len(sig.parameters) == 0, \
            f"reset_master_key_cache 应该无参，actual params: {list(sig.parameters)}"


class TestMasterKeyPersistedAcrossRestarts:
    """迭代 #82 — 用户审计报告 (2026-07-05) 指出 #72 模块级缓存在 dev.bat
    `--reload` 工作流下完全无效：uvicorn --reload 每保存一次代码文件就
    重启子进程，模块级 `_dev_master_key` 跟着清空 → 每次 reload 都生成
    新 key → 已加密的 Provider key **永久失效** ("昨天还好好的今天突然
    全部 Provider 解密失败")。错误信息只有"解密失败"，用户完全无法定位。

    正确修法：dev 模式首次生成后写到 backend/data/.dev_master_key
    (gitignored)，下次启动 / --reload 自动重启时读回同个 key。
    env MASTER_KEY 仍是 source-of-truth（运维改了 env 自动覆盖）。

    加 invariant test 锁死：模拟"进程重启"（重置模块缓存）→ 仍能从
    磁盘拿到同一个 key。
    """
    def _clean_dev_state(self, monkeypatch):
        """清掉 MASTER_KEY env + 模块缓存 + 磁盘文件。"""
        from app import security
        monkeypatch.delenv("MASTER_KEY", raising=False)
        monkeypatch.setattr(security, "_dev_master_key", None)
        # 删除磁盘持久化文件（如果存在）
        if security._DEV_MASTER_KEY_PATH.exists():
            monkeypatch.setattr(
                security, "_DEV_MASTER_KEY_PATH",
                security._DEV_MASTER_KEY_PATH.with_name(
                    security._DEV_MASTER_KEY_PATH.name + ".bak"
                ),
            )

    def test_dev_key_persisted_to_disk_on_first_generation(self, monkeypatch, tmp_path):
        """行为测试：dev 模式首次生成 key 必须写到磁盘（#82）。"""
        from app import security
        # 重定向持久化路径到 tmp_path（避免污染 backend/data/）
        test_path = tmp_path / ".dev_master_key"
        monkeypatch.setattr(security, "_DEV_MASTER_KEY_PATH", test_path)
        self._clean_dev_state(monkeypatch)

        from app.security import get_master_key
        key1 = get_master_key()
        # 文件必须存在
        assert test_path.exists(), (
            f"dev master key 必须持久化到 {test_path}（#82 --reload 安全）"
        )
        # 文件内容必须跟返回的 key 一致
        persisted = test_path.read_text(encoding="utf-8").strip().encode("ascii")
        assert persisted == key1, (
            f"持久化的 key 必须跟 get_master_key() 返回一致，"
            f"实际 {persisted[:20]}... vs {key1[:20]}..."
        )

    def test_dev_key_survives_simulated_process_restart(self, monkeypatch, tmp_path):
        """核心 #82 测试：模拟 uvx --reload（清空模块缓存）→ 仍能解密旧 ciphertext。

        这是用户报告的具体场景：保存代码 → uvicorn 重启 → 重启后解密之前
        填的 Provider key。

        行为测试方案：
          1. 进程 1：get_master_key() → 拿到 key_A，写盘
          2. 用 key_A 加密 plaintext → ciphertext
          3. 模拟进程重启：清空 _dev_master_key cache（reload 触发）
          4. 进程 2：get_master_key() → 从盘读到 key_A（应该一致）
          5. decrypt(ciphertext) 必须成功
        """
        from app import security
        test_path = tmp_path / ".dev_master_key"
        monkeypatch.setattr(security, "_DEV_MASTER_KEY_PATH", test_path)
        self._clean_dev_state(monkeypatch)

        from app.security import get_master_key, encrypt_api_key, decrypt_api_key
        # 进程 1：拿 key + 加密
        key1 = get_master_key()
        plaintext = "sk-test-real-key-12345"
        ciphertext = encrypt_api_key(plaintext)
        # 模拟 --reload 重启：清空模块缓存
        monkeypatch.setattr(security, "_dev_master_key", None)
        # 进程 2：拿 key（应该从磁盘读回同一个）+ 解密
        key2 = get_master_key()
        assert key2 == key1, (
            f"--reload 重启后 key 必须保持一致（#82 核心需求），"
            f"key1[:20]={key1[:20]!r} vs key2[:20]={key2[:20]!r}"
        )
        # 关键：重启后能解密之前写的 ciphertext（这是用户报告的核心痛点）
        decrypted = decrypt_api_key(ciphertext)
        assert decrypted == plaintext, (
            f"--reload 重启后必须能解密之前的密文（#82），"
            f"实际 decrypted={decrypted!r}, 期望={plaintext!r}"
        )

    def test_env_master_key_overrides_persisted_file(self, monkeypatch, tmp_path):
        """覆盖关系测试：env MASTER_KEY 必须胜过磁盘持久化（source-of-truth）。

        防止用户设了 env 但磁盘有旧 dev key 时静默用旧 key——这种情况下
        新 ciphertext 用新 env key 加密，但解密时读的是旧 dev key 会爆。
        """
        from app import security
        import base64, secrets
        test_path = tmp_path / ".dev_master_key"
        monkeypatch.setattr(security, "_DEV_MASTER_KEY_PATH", test_path)
        # 先在磁盘写一个 dev key（模拟"上次 dev 模式生成的"）
        old_key = base64.urlsafe_b64encode(secrets.token_bytes(32))
        test_path.write_text(old_key.decode("ascii"), encoding="utf-8")
        # 清掉内存缓存，确保走 env+disk 解析路径
        monkeypatch.setattr(security, "_dev_master_key", None)
        # 设置 env MASTER_KEY（不同值）
        new_key = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode()
        monkeypatch.setenv("MASTER_KEY", new_key)
        from app.security import get_master_key
        result = get_master_key().decode("ascii")
        assert result == new_key, (
            f"env MASTER_KEY 必须胜过磁盘 dev key（#82 — source-of-truth），"
            f"实际 {result[:20]}... 期望 {new_key[:20]}..."
        )
        # 磁盘 dev key 不应该被删除（让用户能切回 dev 模式仍用它）——不验证

    def test_corrupted_disk_file_regenerates(self, monkeypatch, tmp_path):
        """磁盘文件损坏时必须重新生成 + 覆盖（#82 — 容错）。"""
        from app import security
        test_path = tmp_path / ".dev_master_key"
        monkeypatch.setattr(security, "_DEV_MASTER_KEY_PATH", test_path)
        monkeypatch.delenv("MASTER_KEY", raising=False)
        monkeypatch.setattr(security, "_dev_master_key", None)
        # 写一个无效的 key 文件（不是 base64 格式）
        test_path.write_text("not-valid-base64-!!!", encoding="utf-8")
        from app.security import get_master_key
        # 不应该抛；应该重新生成
        key = get_master_key()
        # 校验 key 格式
        import base64
        decoded = base64.urlsafe_b64decode(key)
        assert len(decoded) == 32, (
            f"损坏文件应触发重新生成（#82 容错），生成的 key 必须有效，实际 {key[:20]!r}"
        )
        # 文件应被覆盖为合法 key
        persisted = test_path.read_text(encoding="utf-8").strip().encode("ascii")
        import base64
        decoded_persisted = base64.urlsafe_b64decode(persisted)
        assert len(decoded_persisted) == 32, (
            f"损坏文件应被覆盖为新 key（#82），实际 {persisted[:20]!r}"
        )

    def test_reset_master_key_cache_clears_disk_file(self, monkeypatch, tmp_path):
        """reset_master_key_cache() 必须也清掉磁盘文件（#82 — API 完整）。"""
        from app import security
        test_path = tmp_path / ".dev_master_key"
        monkeypatch.setattr(security, "_DEV_MASTER_KEY_PATH", test_path)
        monkeypatch.delenv("MASTER_KEY", raising=False)
        monkeypatch.setattr(security, "_dev_master_key", None)
        # 先填一个文件
        test_path.write_text("placeholder-key-value-here", encoding="utf-8")
        from app.security import reset_master_key_cache
        reset_master_key_cache()
        # 内存缓存清空
        assert security._dev_master_key is None
        # 文件也应删除
        assert not test_path.exists(), (
            f"reset_master_key_cache() 必须也删磁盘文件（#82），但 {test_path} 仍存在"
        )

    def test_source_has_dev_key_persistence(self):
        """源码扫描：get_master_key() 必须有磁盘持久化 + 加载逻辑（#82 — 防止回退）。"""
        import inspect
        from app import security
        src = inspect.getsource(security.get_master_key)
        # 必须读 dev_master_key 路径
        assert "_DEV_MASTER_KEY_PATH" in src or "dev_master_key" in src.lower(), \
            "get_master_key() 必须读磁盘 dev master key 文件（#82）"
        # 必须有 atomic write 持久化逻辑
        assert "_persist_dev_key" in src or "persist" in src.lower(), \
            "get_master_key() 必须把首次生成的 key 持久化到磁盘（#82）"
        # 必须优先 env 而非文件
        if src.find("env_key = os.environ.get") < src.find("_load_persisted_dev_key"):
            pass  # env 在前，OK
        else:
            # env 必须在持久化之前检查
            assert False, "env MASTER_KEY 必须在持久化路径之前检查（source-of-truth）"

    def test_dev_key_path_is_gitignored(self):
        """dev master key 文件路径必须被 .gitignore 覆盖（#82 — 不能误提交）。"""
        from pathlib import Path
        repo_root = Path(REPO_ROOT)  # backend/tests → backend → repo_root
        # .gitignore 必须包含 .dev_master_key
        gi = repo_root / ".gitignore"
        content = gi.read_text(encoding="utf-8")
        assert ".dev_master_key" in content, (
            f".gitignore 必须包含 .dev_master_key（#82 — 防误提交）"
        )
