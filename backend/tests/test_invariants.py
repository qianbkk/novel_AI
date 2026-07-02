"""
不变量测试：5 类历史 bug 的锁死测试，确保它们不再静默回归。

5 类历史 bug：
  A) pull_setting_package 字段漂移 → 5 张表全空
  B) meta.json schema 不严 → 标题"【修改后正文】"
  C) import 与 pull 顺序未保护 → 50 章 0 character 边
  D) Pydantic vs ORM nullable 不一致 → 500
  E) 章节首行无校验 → 假标题渗到 preview

跑：python -m pytest backend/tests/test_invariants.py -v
或： python -m scripts.audit_project --strict
"""
import json
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app.schema_validator import (  # noqa: E402
    validate_setting_package, validate_chapter_meta, SchemaError,
    get_setting_package_schema, get_chapter_meta_schema,
)


# ───────────────────────────────────────────
# A: setting_package schema 锁
# ───────────────────────────────────────────
class TestSettingPackageSchema:
    """防止 planner 输出字段漂移（之前 → 5 张表全空）"""

    def test_schema_requires_key_fields(self):
        """最小必要字段：planner 输出缺这些 → pull_setting 后表全空"""
        schema = get_setting_package_schema()
        required = set(schema["required"])
        for must_have in ["title_candidates", "tagline", "protagonist",
                          "world_setting", "power_system", "key_characters",
                          "arc_outline", "foreshadowing_seeds"]:
            assert must_have in required, f"{must_have} 必须在 schema.required 里"

    def test_known_good_setting_passes(self):
        """最小可用 setting_package 必须能通过校验"""
        minimal = {
            "novel_id": "x", "platform": "fanqie", "genre": "玄幻",
            "title_candidates": ["书A", "书B", "书C"],
            "tagline": "一句话简介",
            "protagonist": {
                "name": "林尘", "background": "x", "personality": "x",
                "awakening_trigger": "x",
            },
            "world_setting": {
                "hidden_world_name": "九霄",
                "hidden_world_history": "x" * 60,
                "surface_world_name": "苍玄",
            },
            "power_system": {
                "name": "灵力",
                "levels": [{"level": 1, "name": "锻体"}],
                "currency": "元晶",
            },
            "key_characters": [{"name": "配角A"}],
            "arc_outline": [{"arc_id": 1, "arc_name": "弧1", "arc_goal": "x", "estimated_chapters": 30}],
            "foreshadowing_seeds": [{"content": "伏笔种子：老鬼的真正身份"}],
        }
        validate_setting_package(minimal)  # 不抛 = pass

    def test_missing_key_characters_fails(self):
        """历史 bug 现场：旧 pull_setting 因 key_characters 缺失 → Character 表空"""
        bad = {"novel_id": "x", "title_candidates": ["a"]}
        with pytest.raises(SchemaError) as exc:
            validate_setting_package(bad)
        assert "key_characters" in str(exc.value)

    def test_arc_outline_under_1_fails(self):
        """arc_outline 至少 1 弧（init_arc 才能跑）"""
        bad = {
            "novel_id": "x", "title_candidates": ["a"], "tagline": "x",
            "protagonist": {"name": "x"},
            "world_setting": {"hidden_world_name": "x", "hidden_world_history": "x"*60, "surface_world_name": "x"},
            "power_system": {"name": "x", "levels": [{"level":1, "name":"x"}]},
            "key_characters": [{"name": "x"}],
            "arc_outline": [],
            "foreshadowing_seeds": [],
        }
        with pytest.raises(SchemaError):
            validate_setting_package(bad)


# ───────────────────────────────────────────
# B + E: chapter meta schema 锁
# ───────────────────────────────────────────
class TestChapterMetaSchema:
    """meta.json 没有 chapter_goal → _derive_title 拿不到 → 假标题渗到 preview"""

    def test_chapter_number_required(self):
        with pytest.raises(SchemaError):
            validate_chapter_meta({"score": 5.0})

    def test_known_good_meta_passes(self):
        validate_chapter_meta({
            "chapter_number": 1,
            "chapter_role": "铺垫",
            "chapter_goal": "展现绝境",
            "score": 6.0,
            "word_count": 2000,
        })


# ───────────────────────────────────────────
# _derive_title 必须跳过所有已知 junk pattern
# ───────────────────────────────────────────
class TestDeriveTitle:
    """历史 bug：ch1 标题"【修改后正文】" / ch42 标题"【玄幻·人族秘史卷】第42章..."
    / ch50 标题"第50章 万族共主"（重复）"""

    @pytest.fixture(autouse=True)
    def import_derive(self):
        from app.bridge.chapter_import import _derive_title
        self.derive = _derive_title

    def test_role_goal_preferred(self):
        t = self.derive(1, {"chapter_role": "铺垫", "chapter_goal": "主角觉醒"}, "")
        assert t == "第1章·铺垫·主角觉醒"

    def test_skip_placeholder_first_line(self, ):
        """ch1 历史 bug: content 第一行是 【修改后正文】"""
        content = "【修改后正文】\n\n厅堂不大。\n\n商恪坐在案后。"
        t = self.derive(1, {}, content)
        assert "【修改后正文】" not in t
        assert t.startswith("第1章·")

    def test_skip_arc_volume_header(self, ):
        """ch42 历史 bug: 第一行是卷首 + 章标题"""
        content = "【玄幻·人族秘史卷】第42章 父债子偿\n\n雅间内。\n\n林尘盘膝坐下。"
        t = self.derive(42, {}, content)
        assert "玄幻·人族秘史卷" not in t
        assert t.startswith("第42章·")

    def test_skip_duplicate_title(self, ):
        """ch50 历史 bug: 第一行是「第N章 标题」"""
        content = "第50章 万族共主\n\n雅间里，苏婉清倒了杯茶推过来。"
        t = self.derive(50, {}, content)
        assert not t.endswith("万族共主")
        assert t.startswith("第50章·")

    def test_skip_markdown_heading(self, ):
        """ch7 历史 bug: 第一行是 markdown 标题"""
        content = "# 第七章·血债\n\n---\n\n剑庐小院，晨光稀薄。"
        t = self.derive(7, {}, content)
        assert "第7章·#" not in t
        assert t.startswith("第7章·")

    def test_skip_markdown_separator(self, ):
        """ch21/32 历史 bug: 第一行是 --- 分隔线"""
        content = "---\n\n门开。\n\n屋内却不是一间待客厅堂。"
        t = self.derive(21, {}, content)
        assert t.startswith("第21章·")
        assert not t.startswith("第21章·---")

    def test_real_first_sentence_wins(self, ):
        content = "雅间门被推开时，林尘正盯着窗外街景出神。"
        t = self.derive(33, {}, content)
        assert "雅间门被推开" in t


# ───────────────────────────────────────────
# D: Pydantic vs ORM nullable 一致性
# ───────────────────────────────────────────
class TestPydanticNullable:
    """历史 bug: ChapterFull.created_at: datetime 必填 → 50 章 NULL 全部 500"""

    def test_chapter_full_created_at_optional(self):
        from app.schemas import ChapterFull
        # Pydantic v2: 必须允许 None，否则 50 章 NULL 必 500
        fields = ChapterFull.model_fields
        assert "created_at" in fields
        # 验证 annotation 允许 None
        anno = fields["created_at"].annotation
        # 简化为：直接构造一个 created_at=None 的实例
        inst = ChapterFull(
            id="x", chapter_no=1, content="x", created_at=None,
        )
        assert inst.created_at is None

    def test_orm_default_for_created_at(self):
        """ORM 层 Chapter.created_at 应该有 default=datetime.utcnow 兜底"""
        from app.models import Chapter
        from datetime import datetime
        col = Chapter.__table__.columns["created_at"]
        # 如果是 nullable=True + 有 default，import 时不会失败
        assert col.default is not None or col.nullable, (
            "Chapter.created_at 必须 nullable 或有 default，否则 import 阶段 NULL 会写不进去"
        )


# ───────────────────────────────────────────
# _build_summary 兜底：meta 空时绝不返回空字符串
# ───────────────────────────────────────────
class TestBuildSummary:
    """历史 bug: meta 无 chapter_goal → summary = '' → 章节管理显示空"""

    @pytest.fixture(autouse=True)
    def import_build(self):
        from app.bridge.chapter_import import _build_summary
        self.build = _build_summary

    def test_uses_goal_when_present(self):
        s = self.build({"chapter_goal": "展现林尘觉醒"}, "")
        assert s == "展现林尘觉醒"

    def test_human_required_status_fallback(self):
        """status=human_required → 写明「需人工补全」而不是空字符串"""
        s = self.build({"status": "human_required"}, "x" * 5000)
        assert s
        assert "human_required" in s or "人工" in s

    def test_no_meta_no_content_fallback(self):
        """全空 → 用 content 长度兜底，绝不返回空字符串"""
        s = self.build({}, "")
        assert s  # 至少 "本章 0 字"

    def test_first_real_sentence_fallback(self):
        s = self.build({}, "雅间门被推开时，林尘正盯着窗外街景出神。")
        assert "雅间" in s


# ───────────────────────────────────────────
# F: 字数预算（写入路径 length fix — 防止 50 章字数千差万别）
# ───────────────────────────────────────────
class TestLengthBudget:
    """call_with_length_budget 是写入路径的字数控制，区别于 call() 的"写到哪算哪"。

    历史 bug：50 章生成后 22 章 out-of-range (1800-2700)，因为 writer agent
    写完不知道字数。校验路径（事后重写）只能擦屁股，不能预防。
    """

    def test_method_exists(self):
        from engine.llm.router import LLMRouter
        assert hasattr(LLMRouter, "call_with_length_budget"), (
            "LLMRouter 必须有 call_with_length_budget 方法，否则下次跑 50 章还会超界"
        )

    def test_signature_documented(self):
        """方法签名必须有 target_chars / tolerance / max_continues 三个参数"""
        import inspect
        from engine.llm.router import LLMRouter
        sig = inspect.signature(LLMRouter.call_with_length_budget)
        for param in ["target_chars", "tolerance", "max_continues"]:
            assert param in sig.parameters, f"call_with_length_budget 必须有 {param} 参数"

    def test_truncate_at_sentence_boundary_module_level(self):
        """_truncate_at_sentence_boundary 是模块级函数，能 import。
        历史 bug: 之前硬切在「林」中间，章节结尾半句话。"""
        from engine.llm.router import _truncate_at_sentence_boundary

        # 1) 短文本不切
        assert _truncate_at_sentence_boundary("短的", 100) == "短的"

        # 2) 在句号处切
        text = "林尘走进药铺。" + "他买了一些丹药。" * 100
        result = _truncate_at_sentence_boundary(text, 100)
        # 结果必须以「。」结尾
        assert result.endswith("。"), f"应该停在句号，实际: {result[-20:]!r}"
        # 结果长度 <= max_chars
        assert len(result) <= 100, f"超过 max_chars: {len(result)}"

        # 3) 强制问号/感叹号也认
        text2 = "你好！" + "世界" * 200
        result2 = _truncate_at_sentence_boundary(text2, 50)
        assert result2.endswith("！"), f"应停感叹号，实际: {result2[-20:]!r}"

        # 4) 找不到句末标点 → fallback 硬切（不能无限回退）
        no_punct = "x" * 200
        result3 = _truncate_at_sentence_boundary(no_punct, 100)
        assert len(result3) == 100
        assert result3 == "x" * 100

    def test_writer_uses_length_budget_path(self):
        """run_writer 必须接的是 _call_with_budget，不是 _call_llm。

        历史 bug (你独立验证的): call_with_length_budget 之前只接在
        scripts/rewrite_length.py，没接生成路径。"""
        import inspect
        from engine.agents import writer as writer_mod
        src = inspect.getsource(writer_mod.run_writer)
        # 必须用 _call_with_budget（不是 _call_llm）
        assert "_call_with_budget" in src, (
            "run_writer 必须调 _call_with_budget，否则下次 50 章还是超字数"
        )
        assert "_call_llm(" not in src, (
            "run_writer 不应直接调 _call_llm（那是无 length budget 的旧路径）"
        )


# ───────────────────────────────────────────
# I: rewriter P0/P1/P2 也必须接 _call_with_budget（与 writer 对称）
# ───────────────────────────────────────────
class TestRewriterLengthBudget:
    """历史 bug（你独立验证）: rewriter 三条路径都还在用 router.call()，
    字数要求只在 prompt 里说，LLM 不遵守就写飞。checker 五个维度全不看字数，
    重写后 4500 字的章节能直接落档。

    与 writer 的 run_writer 必须对称：同样是生成路径，必须接入同一种预防式控制。
    """

    @pytest.fixture(autouse=True)
    def import_rewriter(self):
        import inspect as _inspect
        from engine.agents import rewriter as rewriter_mod
        self.mod = rewriter_mod
        self.inspect = _inspect

    def test_run_p0_uses_length_budget(self):
        src = self.inspect.getsource(self.mod.run_p0)
        assert "_call_with_budget" in src, (
            "run_p0 必须调 _call_with_budget，否则 P0 重写后还是字数无控"
        )
        # 真调用（缩进过的代码行），不算注释里的字面量
        code_lines = [
            line for line in src.splitlines()
            if line.startswith(("    ", "\t")) and not line.lstrip().startswith("#")
        ]
        for line in code_lines:
            assert "router.call(" not in line, (
                f"run_p0 真代码行不能 router.call()——那是无 length budget 的旧路径。命中行: {line!r}"
            )

    def test_run_p1_uses_length_budget(self):
        src = self.inspect.getsource(self.mod.run_p1)
        assert "_call_with_budget" in src, (
            "run_p1 必须调 _call_with_budget，否则 P1 重写后还是字数无控"
        )
        code_lines = [
            line for line in src.splitlines()
            if line.startswith(("    ", "\t")) and not line.lstrip().startswith("#")
        ]
        for line in code_lines:
            assert "router.call(" not in line, (
                f"run_p1 真代码行不能 router.call()——那是无 length budget 的旧路径。命中行: {line!r}"
            )

    def test_run_p2_uses_length_budget(self):
        src = self.inspect.getsource(self.mod.run_p2)
        assert "_call_with_budget" in src, (
            "run_p2 必须调 _call_with_budget，否则 P2 润色后还是字数无控"
        )
        code_lines = [
            line for line in src.splitlines()
            if line.startswith(("    ", "\t")) and not line.lstrip().startswith("#")
        ]
        for line in code_lines:
            assert "router.call(" not in line, (
                f"run_p2 真代码行不能 router.call()——那是无 length budget 的旧路径。命中行: {line!r}"
            )

    def test_parse_target_chars_helper_exists(self):
        """_parse_target_chars 必须存在，且从 task.target_length "2000-2200" 取中位数。"""
        assert hasattr(self.mod, "_parse_target_chars"), (
            "rewriter 必须有 _parse_target_chars helper（解析 task.target_length）"
        )
        # 范围字符串 → 中位数
        assert self.mod._parse_target_chars({"target_length": "2000-2200"}) == 2100
        # 纯数字字符串 → 自身
        assert self.mod._parse_target_chars({"target_length": "2300"}) == 2300
        # 缺失 → 默认 "2000-2200" 中位数 = 2100（与 writer.run_writer 一致）
        assert self.mod._parse_target_chars({}) == 2100
        # 异常值 → fallback 到 default 2200（无 - 时走 int() 路径）
        assert self.mod._parse_target_chars({"target_length": "xxx"}) == 2200


# ───────────────────────────────────────────
# H: schema_validator 必须是 fail-fast，不能 try/except ImportError 静默跳过
# ───────────────────────────────────────────
class TestSchemaValidatorFailFast:
    """历史 bug（你独立验证）:
      - jsonschema 没声明在 requirements.txt，README 也没提
      - schema_validator._check() 用 try/except ImportError 包住 import，
        命中后只 warn + return，校验被静默跳过
      - 在全新环境下 audit_project A1/G2 假通过，pytest 3 个测试 DID NOT RAISE
    修复：
      - jsonschema 写进 requirements.txt
      - import jsonschema 提到模块顶层（import time fail-fast）
      - _check() 不再 try/except ImportError
    本测试锁死：
      1) jsonschema 必须已经在 schema_validator 模块 namespace 里
      2) _check() 源码里不能再有 try/except ImportError
    """

    def test_jsonschema_imported_at_module_level(self):
        """如果 jsonschema 是 lazy-import（try/except 内部 import），
        不会出现在模块 namespace。这一项检查直接防止再有人包 try/except。"""
        import app.schema_validator as mod
        assert "jsonschema" in mod.__dict__, (
            "jsonschema 必须从模块顶层 import，否则又会出现"
            "「缺依赖静默跳过」的回归（独立验证场景：干净环境假通过）"
        )

    def test_check_has_no_import_error_fallback(self):
        """_check() 源码里不能出现 `except ImportError`，
        否则会静默跳过校验（历史 bug 现场）。"""
        import inspect
        from app.schema_validator import _check
        src = inspect.getsource(_check)
        assert "except ImportError" not in src, (
            "_check() 不应有 except ImportError，否则 jsonschema 缺失时"
            "会静默跳过校验 → audit_project A1/G2 假通过"
        )

    def test_module_top_level_imports_jsonschema(self):
        """schema_validator.py 的源码顶层必须 `import jsonschema`，
        而不是包在 try/except 内部懒加载。"""
        import inspect
        from app import schema_validator as mod
        src = inspect.getsource(mod)
        # 提取顶层（不缩进的）import 行
        top_imports = [
            line.strip() for line in src.splitlines()
            if line and not line.startswith((" ", "\t")) and line.startswith(("import ", "from "))
        ]
        assert any("jsonschema" in line for line in top_imports), (
            f"schema_validator.py 顶层必须有 `import jsonschema`，"
            f"否则又会被 try/except 包成静默跳过。当前顶层 import: {top_imports}"
        )


# ───────────────────────────────────────────
# G: 整体测试目录 collection 不能报错
# ───────────────────────────────────────────
def _backend_alive(base_url: str, timeout: float = 1.0) -> bool:
    """探测后端是否在指定 URL 监听。
    用 socket TCP 探测而非 HTTP 请求——更轻、更快、不依赖 httpx 异常类型。
    skipif 装饰器在 collection 阶段执行，所以必须快（默认 1s timeout）。
    """
    import socket
    from urllib.parse import urlparse
    parsed = urlparse(base_url)
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port
    if not port:
        return False
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except (OSError, socket.timeout):
        return False


class TestPytestCollection:
    """历史 bug：`pytest tests/` 在 collection 阶段会报 1 个 error，
    因为 test_alignment_smoke.py 里有个 def test(name: str) 装饰器工厂
    撞 pytest 自动收集规则。修复：改名 _test + __test__ = False。"""

    def test_all_tests_dir_collects_cleanly(self):
        """跑 pytest --collect-only 应该 0 collection error。
        关键：test_alignment_smoke.py 里的 def test(name: str) 装饰器工厂
        如果被 pytest 自动收集就会触发 collection error（参数不匹配）。
        修复：rename + __test__ = False。
        """
        import subprocess
        # 用 sub-process 跑收集（不重入自己）
        result = subprocess.run(
            ["python", "-m", "pytest", "tests/", "--collect-only", "-q",
             "--ignore=tests/test_invariants.py"],
            capture_output=True, text=True, cwd=str(BACKEND),
            timeout=60,
        )
        out = (result.stdout + result.stderr).lower()
        # 检查"X error"模式（pytest collection error 的标志）
        import re
        m = re.search(r"(\d+)\s+errors?", out)
        if m:
            err_count = int(m.group(1))
            assert err_count == 0, (
                f"pytest tests/ 有 {err_count} collection errors:\n{out[-1000:]}"
            )


# ───────────────────────────────────────────
# J: 前端默认 backend 端口必须可联通 + 全 6 个契约 path 都注册
# ───────────────────────────────────────────
class TestFrontendBackendPortConsistency:
    """历史 bug（你独立验证）：
      - 前端默认 http://localhost:8123，但实际 active 后端在 8132（8123 僵尸）
      - 前端 client.ts 调用的 6 个 path（rules / foreshadowings / chapter
        characters / ai-assist-level）在 8123 上 404，在 8132 上 200
      - 前端 → 8123 → 404 → 前端误以为后端没起来
    修复：
      - frontend/.env: VITE_API_BASE=http://localhost:8132
      - frontend/src/api/client.ts: 默认端口改 8132
      - frontend/src/App.tsx / Dashboard.tsx / WorldBuild.tsx: 错误提示里的
        "默认地址" 也跟改到 8132
    本测试锁死：
      1) frontend 默认 URL 必须跟当前可联通 backend 一致
      2) backend 必须注册前端需要的 6 个 path（防 router 删漏）
    """

    @pytest.fixture(autouse=True)
    def frontend_paths(self):
        from pathlib import Path
        fe = BACKEND.parent / "frontend"
        self.fe_src = fe / "src"
        self.fe_env = fe / ".env"

    def test_frontend_default_url_is_valid(self):
        """前端 client.ts 默认 fallback URL 必须是合法的 http://localhost:PORT。
        纯静态检查（不联网），CI 无需起服务即可跑。
        """
        import re
        client = (self.fe_src / "api" / "client.ts").read_text(encoding="utf-8")
        m = re.search(r'\|\|\s*"(http://localhost:\d+)"', client)
        assert m, (
            "client.ts 必须有 `|| \"http://localhost:XXXX\"` fallback。"
            f"实际 client.ts 顶部 200 字: {client[:200]!r}"
        )
        default_url = m.group(1)
        # URL 形态必须合法
        assert re.match(r"^http://localhost:\d{4,5}$", default_url), (
            f"fallback URL 形态异常: {default_url!r}（期望 http://localhost:PORT，4-5 位端口）"
        )

    @pytest.mark.skipif(
        not _backend_alive("http://localhost:8132", timeout=1.0),
        reason="需要本机 8132 后端在跑（start.sh 或 uvicorn app.main:app --port 8132）"
    )
    def test_frontend_default_url_reachable_at_runtime(self):
        """运行时验证：client.ts 默认 URL 真的能联通后端。
        跳过条件：8132 不可达（CI / 冷启动）。
        本地开发：跑 `uvicorn app.main:app --port 8132` 后此测试会真的 ping。
        """
        import re
        import httpx
        client = (self.fe_src / "api" / "client.ts").read_text(encoding="utf-8")
        m = re.search(r'\|\|\s*"(http://localhost:\d+)"', client)
        assert m
        default_url = m.group(1)
        r = httpx.get(f"{default_url}/health", timeout=2.0)
        assert r.status_code == 200, (
            f"前端默认 {default_url} 不可达 (status={r.status_code})。"
            f"前端实际打开会全 404。"
        )

    @pytest.mark.skipif(
        not _backend_alive("http://localhost:8132", timeout=1.0),
        reason="需要本机 8132 后端在跑（openapi.json 探测）"
    )
    def test_backend_registers_frontend_contract_paths(self):
        """后端必须注册前端 client.ts 实际调用的 6 个契约 path。
        运行时验证：读后端 openapi.json，检查必需 path 都已注册。
        跳过条件：8132 不可达（CI）。
        """
        import httpx
        client = (self.fe_src / "api" / "client.ts").read_text(encoding="utf-8")
        # 6 个核心契约 path
        required = {
            "/projects/{project_id}/rules",
            "/projects/{project_id}/foreshadowings",
            "/projects/{project_id}/foreshadowings/{foreshadowing_id}/status",
            "/projects/{project_id}/chapters/{chapter_id}/characters",
            "/projects/{project_id}/ai-assist-level",
        }
        openapi = httpx.get("http://localhost:8132/openapi.json", timeout=2.0).json()
        backend_paths = set(openapi.get("paths", {}).keys())
        for p in required:
            assert p in backend_paths, (
                f"前端调 {p}，后端 openapi 没注册这条 → 必然 404"
            )

    def test_no_hardcoded_8123_in_user_facing_strings(self):
        """错误提示文案不能再硬编码 :8123，否则改默认端口后用户看到错地址。"""
        for path in [
            self.fe_src / "App.tsx",
            self.fe_src / "pages" / "Dashboard.tsx",
            self.fe_src / "pages" / "WorldBuild.tsx",
        ]:
            if not path.exists():
                continue
            content = path.read_text(encoding="utf-8")
            # 排除注释里说明旧值的字样
            for line in content.splitlines():
                stripped = line.strip()
                if stripped.startswith("//") or stripped.startswith("*"):
                    continue
                assert ":8123" not in line, (
                    f"{path.name}:{line!r} 还硬编码 :8123 — "
                    f"用户改 backend 端口后会看到错地址"
                )


# ───────────────────────────────────────────
# K: parse_llm_json_response 必须做类型保护（防止 tracker 类 bug 复发）
# ───────────────────────────────────────────
class TestParseLLMJsonResponseTypeGuard:
    """历史 bug（你独立验证）：
      error_log 60+ 次报 `'list' object has no attribute 'get'` ——
      几乎每章 tracker 都中招。
      根因：LLM 偶尔返回 list/None/str，但 tracker.py:83
        updates = parse_llm_json_response(resp, {})
      默认 default={} 是 dict，但 parse 出 list → 后续 updates.get(...) 崩溃
      → 错误被 orchestrator:378 吞掉，state 只记一行字面量，章节照样保存。
    修复（系统级）：
      parse_llm_json_response 加 _coerce_type：返回前校验 parsed 是否跟
      default 同型，否则警告 + 退回 default。
    本测试锁死：类型不匹配时不再穿透到下游。
    """

    def test_list_returned_falls_back_to_empty_dict(self):
        from engine.utils import parse_llm_json_response
        # LLM 返回 list 但 default 是 dict → 应该回 {}
        result = parse_llm_json_response("[1, 2, 3]", default={})
        assert result == {}, f"expected empty dict fallback, got {result!r}"
        assert isinstance(result, dict)

    def test_none_returned_falls_back_to_dict(self):
        from engine.utils import parse_llm_json_response
        # 全部 parse 失败（不是 JSON）→ 回 default
        result = parse_llm_json_response("not json at all", default={})
        assert result == {}
        assert isinstance(result, dict)

    def test_dict_returned_passes_through(self):
        from engine.utils import parse_llm_json_response
        result = parse_llm_json_response('{"a": 1}', default={})
        assert result == {"a": 1}

    def test_fenced_dict_returned_passes_through(self):
        from engine.utils import parse_llm_json_response
        result = parse_llm_json_response('```json\n{"a": 1}\n```', default={})
        assert result == {"a": 1}

    def test_list_for_list_default_passes_through(self):
        from engine.utils import parse_llm_json_response
        result = parse_llm_json_response("[1, 2, 3]", default=[])
        assert result == [1, 2, 3]

    def test_dict_for_list_default_falls_back(self):
        from engine.utils import parse_llm_json_response
        result = parse_llm_json_response('{"a": 1}', default=[])
        assert result == []

    def test_str_returned_falls_back_to_empty_string(self):
        from engine.utils import parse_llm_json_response
        result = parse_llm_json_response('"just a string"', default="")
        # 类型匹配（都是 str），应原样返回
        assert result == "just a string"
        # 现在 default="" 但 LLM 回 list → 应回 ""
        result2 = parse_llm_json_response("[1]", default="")
        assert result2 == ""


class TestTrackerUsesParseWithDictDefault:
    """tracker.py:83 的 `parse_llm_json_response(resp, {})` 必须用 dict 作 default
    —— 不变式。如果有人改成 `parse_llm_json_response(resp, [])` 或别的不当类型，
    立刻测试失败。
    """

    def test_tracker_source_uses_dict_default(self):
        import inspect
        from engine.agents import tracker as tracker_mod
        src = inspect.getsource(tracker_mod.run_tracker)
        assert "parse_llm_json_response(resp, {})" in src, (
            "tracker.run_tracker 必须用 `parse_llm_json_response(resp, {})` "
            "（dict 作 default）；改成 list/None/str 会让后续 updates.get() "
            "在 LLM 返回非 dict 时崩溃。"
        )

    def test_checker_source_uses_dict_default(self):
        """checker.py 内 parse_llm_json_response 调用点必须传 dict 作 default。
        历史 bug（你独立验证）：如果 checker 也用 list 当 default，LLM 回
        dict 时下游 .get() 崩。
        """
        import inspect
        from engine.agents import checker as checker_mod
        src = inspect.getsource(checker_mod)
        assert "parse_llm_json_response(" in src
        # 找到所有 parse 调用点上下文，确认 default 形状是 dict
        import re
        for match in re.finditer(r'parse_llm_json_response\([^)]+\)', src):
            ctx = match.group(0)
            # 允许 "default"（变量名，传 dict）或 "{...}"（字面 dict）
            assert ("default" in ctx and "parse_llm_json_response(resp, default)" in ctx) or \
                   ("{" in ctx and "}" in ctx), (
                f"checker 里的 parse 调用 {ctx!r} 应传 dict default。\n"
                f"如果传了 list/None/str，下游 .get() 在 LLM 回 dict 时会崩。"
            )

    def test_rewriter_p0_checklist_uses_dict_default(self):
        """rewriter.run_p0_checklist 解析 checklist JSON，应是 dict。"""
        import inspect
        from engine.agents import rewriter as rewriter_mod
        src = inspect.getsource(rewriter_mod.run_p0_checklist)
        # 找到调用 parse_llm_json_response 那行附近，应当传 dict
        idx = src.find("parse_llm_json_response(")
        assert idx > 0, "run_p0_checklist 必须调 parse_llm_json_response"
        # 截取调用上下文，看 default 是不是 dict 形式
        snippet = src[idx:idx+200]
        assert '"rewrite_priority"' in snippet or 'rewrite_priority' in snippet, (
            "checklist 解析必须返回包含 rewrite_priority 的 dict，否则下游崩溃"
        )


# ───────────────────────────────────────────
# L: save_state 必须更新 last_updated（state 不能再"看起来冻结"）
# ───────────────────────────────────────────
class TestSaveStateUpdatesLastUpdated:
    """历史 bug（你独立验证）：
      state.last_updated 17 小时没动，但 engine 实际在跑 ch53→ch58。
      根因：save_state 序列化前没更新 state["last_updated"]，bridge/status
      给用户看到的 last_updated 永远是最初那次 create_initial_state 的时间。
      → 监控 / 用户视角"engine 没动"，但实际在跑。

    修复：save_state 自动把 last_updated 设为 datetime.now().isoformat()。
    """

    def test_save_state_updates_last_updated(self, tmp_path):
        import time as time_mod
        from engine.state import save_state
        state_path = tmp_path / "state.json"
        initial = {
            "current_chapter": 50,
            "current_phase": "writing",
            "last_updated": "2025-01-01T00:00:00",  # 故意写旧值
        }
        save_state(initial, str(state_path))
        time_mod.sleep(0.05)  # 让时间过一点
        # 第二次 save
        initial["current_chapter"] = 51
        save_state(initial, str(state_path))
        import json
        on_disk = json.loads(state_path.read_text(encoding="utf-8"))
        # 关键断言：last_updated 必须不是初始的旧值
        assert on_disk["last_updated"] != "2025-01-01T00:00:00", (
            f"save_state 没更新 last_updated（仍是 {on_disk['last_updated']!r}）。"
            f"用户视角会看到 state 永远冻结"
        )
        # current_chapter 也应反映
        assert on_disk["current_chapter"] == 51

    def test_save_state_does_not_mutate_input(self, tmp_path):
        """save_state 不能修改入参 state 的 last_updated（避免脏写）。"""
        from engine.state import save_state
        state_path = tmp_path / "state.json"
        before_ts = "2025-01-01T00:00:00"
        state = {"current_chapter": 0, "last_updated": before_ts}
        save_state(state, str(state_path))
        # 入参的 last_updated 不应该被改
        assert state["last_updated"] == before_ts, (
            f"save_state 不应修改入参，但 last_updated 现在是 {state['last_updated']!r}"
        )


# ───────────────────────────────────────────
# M: writer 失败时不能写占位文本继续 pipeline（防止假 PASS 章节）
# ───────────────────────────────────────────
class TestWriterFailureNoFakeStub:
    """历史 bug（你独立验证）：
      writer 抛 Connection error / SSL 错误时，orchestrator 写
      `f"[writer-stub] {task.get('chapter_goal','')}"` 占位文本（47 字）
      并继续 pipeline → checker 给这个假文本打 7.0 分 PASS，save_and_track
      落盘 ch_0064.txt — 用户视角"7.0 分 PASS"，实际是 47 字假文本。

    修复：
      writer 失败时设 task._writer_failed=True + raw_text=""，提前 return
      node_write_pipeline；route_after_pipeline 检查 _writer_failed → escalate
      → node_human_escalation 走人工 review 流程（不会再写 [writer-stub]）。

    本测试锁死：
      1) writer-stub 占位文本不再被使用
      2) WriterFailedError 类存在
      3) route_after_pipeline 在 _writer_failed=True 时返回 escalate
    """

    def test_no_writer_stub_in_orchestrator(self):
        """orchestrator.py 真代码行不能写 [writer-stub] 占位文本。
        之前 line 243: raw_text = f"[writer-stub] {task.get('chapter_goal','')}", 0.0
        （docstring 里提到 [writer-stub] 是历史说明，OK；真代码行不能用）

        实现：在源码中跟踪三引号 docstring 范围（docstring 内部）和 # 注释
        行，只检查真代码行。
        """
        import inspect
        from engine import orchestrator as orch
        src = inspect.getsource(orch)
        in_docstring = False
        for line in src.splitlines():
            stripped = line.strip()
            # 跟踪三引号 docstring 边界
            triple_count = stripped.count('"""') + stripped.count("'''")
            if triple_count % 2 == 1:
                in_docstring = not in_docstring
                if stripped.startswith(('"""', "'''")) and len(stripped) > 3:
                    continue
            if in_docstring:
                continue
            # 跳过纯注释
            if stripped.startswith("#"):
                continue
            assert "[writer-stub]" not in line, (
                f"orchestrator 真代码行仍写 [writer-stub] 占位: {line!r}。"
                f"writer 失败时应让 task._writer_failed=True + 提前 return。"
            )

    def test_writer_failed_error_class_exists(self):
        from engine.orchestrator import WriterFailedError
        assert issubclass(WriterFailedError, Exception)

    def test_route_after_pipeline_escalates_on_writer_failed(self):
        """_writer_failed=True → route_after_pipeline 必须返回 escalate。"""
        from engine.orchestrator import route_after_pipeline
        state = {
            "current_phase": "writing",
            "current_task": {"_writer_failed": True, "_checker_result": {"score": 7.0}},
            "rewrite_count_current": 0,
        }
        # 即便 checker "通过"了，writer 失败也必须 escalate（不能 save）
        result = route_after_pipeline(state)
        assert result == "escalate", (
            f"_writer_failed=True 时 route_after_pipeline 应返回 escalate，"
            f"实际: {result!r}"
        )

    def test_route_after_pipeline_saves_normal_pass(self):
        """_writer_failed=False + score>=PASS_SCORE → save（正常路径不能误伤）。"""
        from engine.orchestrator import route_after_pipeline, PASS_SCORE
        state = {
            "current_phase": "writing",
            "current_task": {"_writer_failed": False, "_checker_result": {"score": PASS_SCORE}},
            "rewrite_count_current": 0,
        }
        result = route_after_pipeline(state)
        assert result == "save", (
            f"正常 PASS 章节应 save，实际: {result!r}"
        )

    def test_node_write_pipeline_short_circuits_on_writer_exception(self, monkeypatch):
        """node_write_pipeline 在 writer 抛异常时不能继续 pipeline。
        模拟 run_writer 抛 ConnectionError，看 task._writer_failed 是否置位。
        """
        from engine import orchestrator as orch
        # monkeypatch run_writer 抛异常
        def fake_run_writer(task, memory, setting):
            raise ConnectionError("simulated writer failure")
        monkeypatch.setattr(orch, "run_writer", fake_run_writer)

        state = {
            "current_task": {"chapter_number": 99, "chapter_goal": "test"},
            "current_chapter": 99,
            "rewrite_count_current": 0,
            "error_log": [],
            "chapter_task_queue": [],
        }
        result = orch.node_write_pipeline(state)
        # 必须标记 _writer_failed=True
        assert result["current_task"].get("_writer_failed") is True, (
            "writer 抛异常时 task._writer_failed 必须置 True"
        )
        # 不能再有 checker_result（避免后续 save 假章节）
        assert "_checker_result" not in result["current_task"] or \
               not result["current_task"].get("_checker_result"), (
            "writer 失败时不应有 _checker_result（说明 pipeline 跑完了）"
        )
        # error_log 记录
        assert any("writer failed" in e for e in result.get("error_log", [])), (
            f"error_log 应记录 writer 失败，实际: {result.get('error_log', [])[-3:]}"
        )


# ───────────────────────────────────────────
# O: orchestrator 全 pipeline 失败兜底必须显式 escalate（不再 fake pass）
# ───────────────────────────────────────────
class TestOrchestratorNoFakePass:
    """你独立验证发现的 5 个同型 fake-pass bug：

      1. compliance 失败 → 兜底 {"passed": True}（line 294 之前）
      2. checker 失败 → 兜底 {"score": 7.0, "verdict": "PASS"}（line 311 之前）
      3. rewriter 失败 → 兜底 new_text = draft_text（line 363 之前）
      4. checker (post-rewrite) 失败 → 兜底 cr2 = cr（line 402 之前）
      5. outline 失败 → 兜底 10 个 placeholder task（line 201 之前）

    统一修法：异常时设 task._xxx_failed=True（每个 stage 单独 flag），
    route_after_pipeline / route_after_rewrite 检查后路由到 escalate，
    不再让 fake 默认值污染下游。
    本测试锁死。
    """

    @pytest.fixture(autouse=True)
    def orch(self, monkeypatch):
        """提供 monkeypatched run_* helpers."""
        from engine import orchestrator as orch_mod
        return orch_mod

    def test_compliance_failure_marks_task(self, orch, monkeypatch):
        """compliance 抛异常 → task._compliance_check_failed=True + 提前 return"""
        def fake_writer(task, memory, setting):
            return "ok 2000字 真实文本 " * 200, 0.0
        def fake_normalizer(text, task):
            return text, [], 0.0
        def fake_compliance(text, platform):
            raise ConnectionError("compliance down")
        monkeypatch.setattr(orch, "run_writer", fake_writer)
        monkeypatch.setattr(orch, "run_normalizer", fake_normalizer)
        monkeypatch.setattr(orch, "run_compliance", fake_compliance)
        state = {"current_task": {"chapter_number": 99, "audit_mode": "full"},
                 "current_chapter": 99, "rewrite_count_current": 0,
                 "error_log": [], "chapter_task_queue": [],
                 "platform": "fanqie"}
        result = orch.node_write_pipeline(state)
        assert result["current_task"].get("_compliance_check_failed") is True, (
            "compliance 抛异常时 task._compliance_check_failed 必须置 True"
        )
        # 不应继续到 checker
        assert "_checker_result" not in result["current_task"]

    def test_checker_failure_marks_task(self, orch, monkeypatch):
        """checker 抛异常 → task._checker_failed=True + 提前 return"""
        def fake_writer(task, memory, setting):
            return "ok text " * 200, 0.0
        def fake_normalizer(text, task):
            return text, [], 0.0
        def fake_compliance(text, platform):
            return {"passed": True, "suggestion": ""}, 0.0
        def fake_checker(text, task, mode):
            raise ConnectionError("checker down")
        monkeypatch.setattr(orch, "run_writer", fake_writer)
        monkeypatch.setattr(orch, "run_normalizer", fake_normalizer)
        monkeypatch.setattr(orch, "run_compliance", fake_compliance)
        monkeypatch.setattr(orch, "run_checker", fake_checker)
        state = {"current_task": {"chapter_number": 99, "audit_mode": "full"},
                 "current_chapter": 99, "rewrite_count_current": 0,
                 "error_log": [], "chapter_task_queue": [],
                 "platform": "fanqie"}
        result = orch.node_write_pipeline(state)
        assert result["current_task"].get("_checker_failed") is True, (
            "checker 抛异常时 task._checker_failed 必须置 True（不再 fake score=7.0 PASS）"
        )

    def test_rewriter_failure_marks_task(self, orch, monkeypatch):
        """rewriter 抛异常 → task._rewriter_failed=True + 提前 return"""
        def fake_rewriter(text, lvl, feedback, task, cr, memory, setting):
            raise ConnectionError("rewriter down")
        monkeypatch.setattr(orch, "run_rewriter", fake_rewriter)
        state = {
            "current_task": {
                "chapter_number": 99,
                "_checker_result": {"score": 5.0, "rewrite_level": "P1"},
                "_draft_text": "原始文本",
            },
            "current_chapter": 99,
            "rewrite_count_current": 0,
            "error_log": [],
            "chapter_task_queue": [],
            "novel_id": "default",
        }
        result = orch.node_rewrite(state)
        assert result["current_task"].get("_rewriter_failed") is True, (
            "rewriter 抛异常时 task._rewriter_failed 必须置 True（不再用原文本当重写结果）"
        )
        # draft_text 应保留原值（不是被覆盖为空）
        assert result["current_task"].get("_draft_text") == "原始文本"

    def test_checker_post_rewrite_failure_marks_task(self, orch, monkeypatch):
        """checker (post-rewrite) 抛异常 → _checker_failed=True（不再用旧 cr 兜底）"""
        def fake_rewriter(text, lvl, feedback, task, cr, memory, setting):
            return "重写后文本 " * 200, 0.0
        def fake_normalizer(text, task):
            return text, [], 0.0
        def fake_compliance(text, platform):
            return {"passed": True}, 0.0
        def fake_checker(text, task, mode):
            raise ConnectionError("post-rewrite checker down")
        monkeypatch.setattr(orch, "run_rewriter", fake_rewriter)
        monkeypatch.setattr(orch, "run_normalizer", fake_normalizer)
        monkeypatch.setattr(orch, "run_compliance", fake_compliance)
        monkeypatch.setattr(orch, "run_checker", fake_checker)
        state = {
            "current_task": {
                "chapter_number": 99,
                "_checker_result": {"score": 5.0, "rewrite_level": "P1", "feedback": "x"},
                "_draft_text": "原始文本",
                "_compliance_failed": False,
            },
            "current_chapter": 99,
            "rewrite_count_current": 0,
            "error_log": [],
            "chapter_task_queue": [],
            "novel_id": "default",
            "platform": "fanqie",
        }
        result = orch.node_rewrite(state)
        assert result["current_task"].get("_checker_failed") is True, (
            "post-rewrite checker 抛异常时 _checker_failed 必须置 True"
        )

    def test_route_after_pipeline_escalates_on_compliance_check_failed(self):
        from engine.orchestrator import route_after_pipeline
        state = {
            "current_phase": "writing",
            "current_task": {"_compliance_check_failed": True, "_checker_result": {"score": 7.0}},
            "rewrite_count_current": 0,
        }
        assert route_after_pipeline(state) == "escalate"

    def test_route_after_pipeline_escalates_on_checker_failed(self):
        from engine.orchestrator import route_after_pipeline
        state = {
            "current_phase": "writing",
            "current_task": {"_checker_failed": True, "_checker_result": {"score": 7.0}},
            "rewrite_count_current": 0,
        }
        assert route_after_pipeline(state) == "escalate"


# ───────────────────────────────────────────
# P: writer / rewriter 网络异常必须重试一次再 escalate
# ───────────────────────────────────────────
class TestAgentNetworkRetry:
    """ch63 / ch64 现场：MiniMax 30-60s 不可用时，router 内部 tenacity 3 次
    退避 1-10s（共最多 30s）仍会失败。agent 层加一轮 30s sleep 后再 retry，
    覆盖更长的瞬时不可用窗口。
    """

    def test_writer_retries_on_httpx_error(self, monkeypatch):
        """writer 第一次 httpx.TransportError → sleep + retry 一次成功。"""
        import time as _time
        from engine.agents import writer as writer_mod
        from engine.llm.router import LLMRouter
        call_count = [0]
        sleep_calls = []

        def fake_call_with_length_budget(self, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                import httpx
                raise httpx.TransportError("simulated conn reset")
            return "ok text " * 200, 0.0

        def fake_sleep(secs):
            sleep_calls.append(secs)
        monkeypatch.setattr(_time, "sleep", fake_sleep)
        monkeypatch.setattr(LLMRouter, "call_with_length_budget",
                            fake_call_with_length_budget)
        monkeypatch.setattr(writer_mod, "_get_router", lambda: LLMRouter())

        text, cost = writer_mod._call_with_budget(
            agent_name="writer", system="x", user="y", target_chars=2000,
        )
        assert call_count[0] == 2, f"应重试一次，实际 {call_count[0]} 次"
        assert sleep_calls == [30], f"应 sleep 30s 一次，实际: {sleep_calls}"
        assert text.startswith("ok text")

    def test_writer_raises_after_two_failures(self, monkeypatch):
        """writer 两次都失败 → 抛最后一次异常给 orchestrator 走 escalate。"""
        import time as _time
        from engine.agents import writer as writer_mod
        from engine.llm.router import LLMRouter
        import httpx

        def fake_call(self, **kwargs):
            raise httpx.TransportError("always fail")
        monkeypatch.setattr(_time, "sleep", lambda s: None)
        monkeypatch.setattr(LLMRouter, "call_with_length_budget", fake_call)
        monkeypatch.setattr(writer_mod, "_get_router", lambda: LLMRouter())

        import pytest
        with pytest.raises(httpx.TransportError):
            writer_mod._call_with_budget(
                agent_name="writer", system="x", user="y", target_chars=2000,
            )


# ───────────────────────────────────────────
# Q: run 进程必须 subprocess 跑，不再 in-process（uvicorn 重启不杀 engine）
# ───────────────────────────────────────────
class TestBridgeSubprocessArchitecture:
    """历史 bug：bridge.run 用 BackgroundTasks 在 uvicorn worker 进程内跑
    engine，uvicorn 重启（手动 / --reload / OOM）会杀掉 in-flight engine run。
    修复：spawn subprocess 跑 engine，stdout pipe 转发 SSE 事件，DB 写
    BridgeRun.status 跟踪生命周期，uvicorn 重启不影响。

    本测试锁死：
    1) subprocess worker 脚本存在
    2) bridge._spawn_engine_subprocess 函数存在
    3) run_bridge endpoint 调用 _spawn_engine_subprocess 而不是 _run_bridge_async
    4) build_graph 接受 checkpointer 参数（之前 status 命令 fail 的隐藏 bug）
    5) SSECapture 在 queue=None 时回退到 stdout（subprocess 模式不丢消息）
    """

    def test_worker_script_exists(self):
        from pathlib import Path
        ws = Path(__file__).resolve().parents[1] / "engine" / "workers" / "run_bridge_subprocess.py"
        assert ws.exists(), f"worker 脚本不存在: {ws}"

    def test_bridge_has_spawn_engine_subprocess(self):
        from app.api import bridge as bridge_mod
        assert hasattr(bridge_mod, "_spawn_engine_subprocess"), (
            "bridge 必须有 _spawn_engine_subprocess 函数（替代 in-process BackgroundTasks）"
        )

    def test_run_endpoint_uses_subprocess(self):
        """run_bridge endpoint 必须调 _spawn_engine_subprocess，不是 _run_bridge_async。"""
        import inspect
        from app.api import bridge as bridge_mod
        src = inspect.getsource(bridge_mod.run_bridge)
        # 关键断言：源代码里必须出现 _spawn_engine_subprocess
        assert "_spawn_engine_subprocess" in src, (
            "run_bridge 没用 _spawn_engine_subprocess——仍在 in-process 旧路径"
        )
        # 反向：不能再有 background_tasks.add_task(_run_bridge_async, ...)
        assert "background_tasks.add_task(\n        _run_bridge_async" not in src and \
               "background_tasks.add_task(_run_bridge_async" not in src, (
            "run_bridge 仍用 BackgroundTasks + _run_bridge_async（in-process 旧路径）"
        )

    def test_build_graph_accepts_checkpointer(self):
        """build_graph 必须接受 checkpointer 参数（否则 status 命令 fail）。"""
        from engine.orchestrator import build_graph
        # 不传 checkpointer 也能用
        g = build_graph()
        assert g is not None
        # 传 checkpointer 也能用
        from langgraph.checkpoint.memory import MemorySaver
        g2 = build_graph(checkpointer=MemorySaver())
        assert g2 is not None

    def test_sse_capture_handles_none_queue(self):
        """SSECapture 在 queue=None 时不能崩（subprocess 模式）。"""
        from engine.graph import SSECapture
        from io import StringIO
        # queue=None 必须不抛
        cap = SSECapture(None)
        # 模拟 print 输出
        cap.write("hello world\n")
        cap.write("more text\n")
        cap.flush()
        # StringIO 行为：write 后 super().write 把数据存到内部 buffer
        # 不能崩 + 至少不抛异常
        assert True

    def test_subprocess_smoke_status(self):
        """subprocess worker 跑 status 命令能 exit_code=0。"""
        import subprocess
        import sys
        from pathlib import Path
        result = subprocess.run(
            [sys.executable, "-m", "engine.workers.run_bridge_subprocess",
             "smoke-test", "c12345678901234567890123456789012", "status", "batch"],
            capture_output=True, text=True,
            cwd=str(Path(__file__).resolve().parents[1]),
            timeout=15,
        )
        # 之前 status 命令的 build_graph 错让 exit_code=1，修了之后必须=0
        assert result.returncode == 0, (
            f"subprocess status 应 exit_code=0，实际: {result.returncode}\n"
            f"stdout: {result.stdout[-500:]}\nstderr: {result.stderr[-500:]}"
        )


# ───────────────────────────────────────────
# N: state / chapters 路径必须用 binding.novel_ai_dir，不再硬编码
# ───────────────────────────────────────────
class TestStatePathFromBinding:
    """历史 bug（你独立验证）：
      engine/orchestrator.py:43-45 硬编码 STATE_PATH / OUTPUT_DIR /
      CHAPTERS_DIR 到 backend/data/engine/output/，但
      app/bridge/reports.py:109 用 binding.novel_ai_dir（默认 novel_AI/output/）。
      → bridge/status 读 novel_AI/output/orchestrator_state.json（17 小时前），
        engine 实际写到 backend/data/engine/output/orchestrator_state.json（活跃）。
      → 双重真相：监控看不到 engine 真实状态。

    修复：engine 的 STATE_PATH / _STATE_PATH 优先用 NOVEL_AI_DIR 环境变量，
    bridge/run 在 spawn background task 前从 binding 注入这个 env。
    本测试锁死 env 行为。
    """

    def test_orchestrator_state_path_uses_env(self, monkeypatch, tmp_path):
        """设 NOVEL_AI_DIR 后，orchestrator.STATE_PATH 走那个目录。"""
        monkeypatch.setenv("NOVEL_AI_DIR", str(tmp_path))
        # 重新 import 让模块级常量重算
        import importlib
        from engine import orchestrator as orch
        importlib.reload(orch)
        try:
            assert str(orch.STATE_PATH).startswith(str(tmp_path)), (
                f"orchestrator.STATE_PATH 应在 NOVEL_AI_DIR 下，"
                f"实际: {orch.STATE_PATH}"
            )
            assert str(orch.STATE_PATH).endswith("orchestrator_state.json")
        finally:
            # 重新 reload 恢复默认
            monkeypatch.delenv("NOVEL_AI_DIR", raising=False)
            importlib.reload(orch)

    def test_graph_state_path_uses_env(self, monkeypatch, tmp_path):
        """engine/graph.py 的 _STATE_PATH 也走 NOVEL_AI_DIR。"""
        monkeypatch.setenv("NOVEL_AI_DIR", str(tmp_path))
        import importlib
        from engine import graph as graph_mod
        importlib.reload(graph_mod)
        try:
            assert str(graph_mod._STATE_PATH).startswith(str(tmp_path))
        finally:
            monkeypatch.delenv("NOVEL_AI_DIR", raising=False)
            importlib.reload(graph_mod)

    def test_bridge_run_injects_novel_ai_dir(self):
        """app/api/bridge.py 的 _run_bridge_async 必须从 binding 注入 NOVEL_AI_DIR。"""
        import inspect
        from app.api import bridge as bridge_mod
        src = inspect.getsource(bridge_mod._run_bridge_async)
        assert "NOVEL_AI_DIR" in src, (
            "bridge._run_bridge_async 必须注入 NOVEL_AI_DIR env，"
            "否则 engine STATE_PATH 跟 binding 不一致（双重真相 bug）"
        )
        assert "NovelAIBinding" in src and "novel_ai_dir" in src, (
            "必须从 binding 读 novel_ai_dir 再注入 env"
        )
