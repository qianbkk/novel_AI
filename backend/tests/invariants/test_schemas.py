"""schemas/ — Phase 3 测试拆分

不变量测试按业务域分文件存放。
原文件位置：tests/test_invariants.py（已替换为 re-export shim）
"""

import json
import sys
from pathlib import Path
import pytest

BACKEND = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(BACKEND))

# ── 原 test_invariants.py 顶部声明的 app.schema_validator 系列 ──
from app.schema_validator import (  # noqa: E402,F401
    validate_setting_package, validate_chapter_meta, SchemaError,
    get_setting_package_schema, get_chapter_meta_schema,
    validate_world_view_rich, validate_character_card, validate_entity_relation_rich,
    get_world_view_rich_schema, get_character_card_schema, get_entity_relation_rich_schema,
)

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


class TestMigrationsIdempotent:
    """最后 #20：migrations.py 是启动时 ALTER TABLE 的关键路径，零测试。

    idempotent 是关键：启动跑两次不应报错（已经 add 过的列不能再 add）。
    """
    def test_run_migrations_is_idempotent(self):
        """连续调 run_migrations 两次不报错（第二次 applied=0）。"""
        from app.migrations import run_migrations
        applied_first = run_migrations()
        applied_second = run_migrations()
        assert applied_second == 0, (
            f"第二次 run_migrations 应 applied=0（idempotent），实际 {applied_second}"
        )

    def test_run_migrations_handles_missing_table_gracefully(self):
        """_column_exists 对不存在的表应返回 False（不抛）。"""
        from app.migrations import _column_exists
        from sqlalchemy import create_engine
        eng = create_engine("sqlite:///:memory:")
        with eng.connect() as conn:
            try:
                result = _column_exists(conn, "nonexistent_table_xyz", "any_col")
                assert result is False
            except Exception as e:
                raise AssertionError(f"_column_exists 不应抛（缺表），实际 {type(e).__name__}: {e}")


class TestWorldViewRichSchema:
    """防 world_view_rich 7 段字段漂移（之前 → 世界观简陋、一段 text）。"""

    def test_schema_requires_all_7_sections(self):
        schema = get_world_view_rich_schema()
        required = set(schema["required"])
        for must_have in ["cosmos", "geography", "history", "society", "technology", "races", "customs"]:
            assert must_have in required, f"{must_have} 必须在 schema.required 里"

    def test_known_good_worldview_passes(self):
        """最小可用 7 段世界观必须能通过校验（每段 ≥30 字）"""
        minimal = {
            "cosmos":     "蓝星与九天之上并存，人间是科技主导的现代都市，修士隐于暗面，" * 1,
            "geography":  "云州、临海、苍莽山脉三足鼎立；州内分七区，每区有独立的风物与宗门。" * 1,
            "history":    "1984 年首次灵气潮汐以来，世界观经历了三波大迭代，逐步形成当前格局。" * 1,
            "society":    "修士与凡人共治，修士内部分九品，每品对应不同的话语权与资源分配。" * 1,
            "technology": "灵气+科技的混合形态：灵力可与电路耦合，催生新型工业体系初具规模。" * 1,
            "races":      "人族 / 古妖族 / 幽冥族三大种族鼎立，下设数十个亚族与部落分支。" * 1,
            "customs":    "祭剑节 / 走火大会 / 长辈赐字；新人入门必须经三年苦修方可下山。" * 1,
        }
        # 每段都超过 30 字
        for k, v in minimal.items():
            assert len(v) >= 30, f"{k} 长度不足"
        validate_world_view_rich(minimal)  # 不抛 = pass

    def test_missing_section_fails(self):
        """7 段缺一就 fail（防止 Planner 漏字段）"""
        bad = {
            "cosmos": "x" * 50,
            "geography": "x" * 50,
            # 缺 history / society / technology / races / customs
        }
        with pytest.raises(SchemaError) as exc:
            validate_world_view_rich(bad)
        assert "history" in str(exc.value) or "society" in str(exc.value)

    def test_section_too_short_fails(self):
        """任何一段 < 30 字就 fail（防止 LLM 偷懒）"""
        bad = {
            "cosmos":     "x" * 50,
            "geography":  "x" * 50,
            "history":    "太短",  # < 30
            "society":    "x" * 50,
            "technology": "x" * 50,
            "races":      "x" * 50,
            "customs":    "x" * 50,
        }
        with pytest.raises(SchemaError) as exc:
            validate_world_view_rich(bad)
        assert "history" in str(exc.value)


class TestCharacterCardSchema:
    """防角色卡字段漂移（之前 → Character 只剩 name + role，看不到任何详情）。"""

    def test_schema_requires_3_top_sections(self):
        """personality / catchphrase / arc 三段必填"""
        schema = get_character_card_schema()
        required = set(schema["required"])
        for must_have in ["personality", "catchphrase", "arc"]:
            assert must_have in required, f"{must_have} 必须在 schema.required 里"

    def test_known_good_card_passes(self):
        """最小可用角色卡必须通过校验"""
        minimal = {
            "basic":       {"gender": "男", "age": 32, "identity": "云州林氏长子"},
            "appearance":  {"height": "182cm", "hair": "短黑", "outfit": "深灰风衣"},
            "personality": {"tags": ["克制", "精算"], "summary": "外表冷峻内心压着火，行动前必先算三步。" * 2},
            "background":  {"origin": "云州林氏", "motivation": "改写林家破产命运", "secret": "前世是 2024 的商业老兵"},
            "abilities":   {"power_name": "先知回响", "current_tier": "一级", "growth_potential": "七阶"},
            "catchphrase": {"lines": ["这局我来开局。", "记住，你是来学习的。"]},
            "props":       {"signature_item": "老旧铜怀表", "companion": "瘸腿狼狗'阿斗'"},
            "arc":         {"start_state": "破产边缘的小商人", "catalyst": "重生回到 2012", "end_state": "云州新一代商盟领袖"},
        }
        validate_character_card(minimal)

    def test_missing_arc_fails(self):
        """缺 arc 段 fail"""
        bad = {
            "personality": {"tags": ["克制"], "summary": "x" * 50},
            "catchphrase": {"lines": ["x"]},
            # 缺 arc
        }
        with pytest.raises(SchemaError) as exc:
            validate_character_card(bad)
        assert "arc" in str(exc.value)

    def test_personality_missing_tags_fails(self):
        """personality.tags 必填（防止 LLM 只写 summary 不写标签）"""
        bad = {
            "personality": {"summary": "x" * 50},  # 缺 tags
            "catchphrase": {"lines": ["x"]},
            "arc":         {"start_state": "x", "catalyst": "x", "end_state": "x"},
        }
        with pytest.raises(SchemaError) as exc:
            validate_character_card(bad)
        assert "tags" in str(exc.value)


class TestEntityRelationRichSchema:
    """防富关系字段漂移（之前 → EntityRelation 只有 from/to/relation/description 一行）。"""

    def test_intensity_bounds(self):
        """intensity 必须在 0-10 之间"""
        bad = {
            "mutual":    True,
            "intensity": 15,  # 越界
            "tags":      ["敌对"],
            "evolution": [{"phase": "开端", "state": "陌生"}],
            "key_events": [{"chapter_hint": "第3章", "event": "初遇"}],
        }
        with pytest.raises(SchemaError) as exc:
            validate_entity_relation_rich(bad)
        assert "intensity" in str(exc.value)

    def test_known_good_relation_passes(self):
        """最小可用富关系必须通过校验"""
        minimal = {
            "mutual":     True,
            "intensity":  9,
            "tags":       ["亲密", "信任"],
            "evolution":  [
                {"phase": "开端", "state": "互有好感"},
                {"phase": "中段", "state": "因破产疏远"},
                {"phase": "结尾", "state": "重逢后共事"},
            ],
            "key_events": [
                {"chapter_hint": "第3章",  "event": "主角劝阻她投资"},
                {"chapter_hint": "第18章", "event": "破产后仍守在身边"},
            ],
        }
        validate_entity_relation_rich(minimal)

    def test_evolution_missing_phase_fails(self):
        """evolution[].phase 必填"""
        bad = {
            "evolution": [{"state": "陌生"}],  # 缺 phase
        }
        with pytest.raises(SchemaError) as exc:
            validate_entity_relation_rich(bad)
        assert "phase" in str(exc.value)


class TestSchemaLenientOnUnion:
    """验证 Phase 3/4 schema 接受 LLM 真实输出（int 替代 str / null 等）。

    历史 bug：CharacterSummaryOut.age 声明为 Optional[str]，但
    stages.py::_CHARACTERS_MOCK 的 basic.age 是整数（如 32），LLM 实际
    生成的也是 int。Pydantic v2 strict 模式拒绝 int → str 隐式转换 →
    GET /projects/{pid}/characters 整接口 500。
    """

    def test_character_summary_age_accepts_int(self):
        """LLM mock 给 age=32 (int)，schema 必须接受"""
        from app.schemas import CharacterSummaryOut
        m = CharacterSummaryOut(id="x", name="y", age=32)  # 不抛
        assert m.age == 32

    def test_character_summary_age_accepts_str(self):
        """老数据 / 文本型 age 也要兼容"""
        from app.schemas import CharacterSummaryOut
        m = CharacterSummaryOut(id="x", name="y", age="32 岁")
        assert m.age == "32 岁"

    def test_character_summary_age_optional(self):
        """card_basic_json 缺失 / age 不存在时，age 应为 None，不抛"""
        from app.schemas import CharacterSummaryOut
        m = CharacterSummaryOut(id="x", name="y")
        assert m.age is None

    def test_character_summary_full_mock_payload(self):
        """完整 _CHARACTERS_MOCK 第一条 (basic.age=32) 能通过 Pydantic 校验"""
        from app.schemas import CharacterSummaryOut
        # 模拟 api/world.py::list_characters 里的取值
        m = CharacterSummaryOut(
            id="abc",
            name="林渊",
            role="主角",
            identity="云州林氏长子",
            age=32,             # int
            gender="男",
        )
        assert m.age == 32
        assert m.gender == "男"
        assert m.identity == "云州林氏长子"

    def test_character_card_out_handles_dict_or_none(self):
        """card 字段允许 None（老项目 fallback），不应抛"""
        from app.schemas import CharacterCardOut
        m1 = CharacterCardOut(id="x", name="y", role="r", card=None)
        assert m1.card is None
        m2 = CharacterCardOut(
            id="x", name="y", role="r",
            card={"basic": {"age": 32, "gender": "男", "identity": "x"}},
        )
        assert m2.card["basic"]["age"] == 32

    def test_character_relation_out_intensity_int(self):
        """intensity 是 int (0-10)"""
        from app.schemas import CharacterRelationOut
        m = CharacterRelationOut(
            id="r1", relation="宿敌", description="x",
            target={"id": "t", "name": "T", "role": "配角"},
            mutual=True, intensity=8,
            tags=["敌对", "仇恨"],
            evolution=[{"phase": "开端", "state": "陌生"}],
            key_events=[{"chapter_hint": "第1章", "event": "相遇"}],
        )
        assert m.intensity == 8
        assert m.tags == ["敌对", "仇恨"]

    def test_worldview_rich_out_minimal(self):
        """rich 可为 None（老项目 fallback），story_core 可为 None"""
        from app.schemas import WorldviewRichOut
        m = WorldviewRichOut(
            rich=None, story_core=None, history_timeline=None,
            fallback_text="legacy", fallback_story_core="legacy core",
        )
        assert m.rich is None
        assert m.fallback_text == "legacy"
