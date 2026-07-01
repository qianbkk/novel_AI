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
