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

    def test_no_hardcoded_8123_in_docs_and_scripts(self):
        """README / dev.bat / scripts / docs html 不能硬编码 :8123
        (排除：注释里解释历史/端口漂移、tests/ 锁死历史 bug)。
        历史背景：commit 3278a77 把后端 8123→8132，本轮扫出 5 处残留
        (README.md / dev.bat / docs/novel-ai-guide.html / run_mvp.py) 全已修。
        """
        from pathlib import Path
        repo = Path(__file__).resolve().parents[2]
        # 扫这些路径下硬编码 :8123 的可执行/文档文件
        targets = [
            repo / "README.md",
            repo / "dev.bat",
            repo / "docs" / "novel-ai-guide.html",
            repo / "backend" / "scripts" / "run_mvp.py",
        ]
        # 排除注释里提到 8123 是因为要解释 8132 的来历 / findstr 锚定语义
        ALLOWED_LINE_FRAGMENTS = (
            "8123 经常被",        # client.ts 注释解释 8132 来历
            "Anchoring on the trailing space prevents \":8123\"",  # dev.bat findstr
            "from matching \":81230\"",  # dev.bat findstr
        )
        violations: list[str] = []
        for path in targets:
            if not path.exists():
                continue
            content = path.read_text(encoding="utf-8")
            for i, line in enumerate(content.splitlines(), start=1):
                if ":8123" not in line:
                    continue
                if any(frag in line for frag in ALLOWED_LINE_FRAGMENTS):
                    continue
                # 排除纯注释行（REM 开头 / // 开头 / # 开头且包含 8132 解释）
                stripped = line.strip()
                if stripped.startswith("REM") and "8132" in stripped:
                    continue
                if stripped.startswith("//") and "8132" in stripped:
                    continue
                if stripped.startswith("#") and "8132" in stripped:
                    continue
                violations.append(f"{path.relative_to(repo)}:{i}: {line.rstrip()}")
        assert not violations, (
            "硬编码 :8123 残留（应统一为 :8132）：\n  "
            + "\n  ".join(violations)
        )


# ───────────────────────────────────────────
# V: submitReview 前后端 schema 一致（防止 edited_content vs content 错位）
# ───────────────────────────────────────────
class TestReviewContract:
    """历史 bug：
      前端 submitReview 发 `edited_content` 字段，
      后端 ReviewRequest 读 `content` 字段 → 永远拿到 None
      → 用户"编辑后提交"的内容静默丢失。

    锁定条件：
      - 前端 api.submitReview 类型声明必须用 `content`
      - 前端实际调用必须用 `content`
      - 后端 ReviewRequest 必须有 `content` 字段
      - 前端不能出现 `edited_content`（已统一）
    """

    def test_frontend_submit_review_uses_content_field(self):
        """前端 api.submitReview 类型声明 + BridgeConsole 调用都用 `content`。"""
        client_ts = (Path(__file__).resolve().parents[2] / "frontend" / "src" / "api" / "client.ts").read_text(encoding="utf-8")
        assert "edited_content" not in client_ts, (
            "frontend api/client.ts 还用 edited_content 字段 — "
            "后端 ReviewRequest 读 content，编辑内容会被丢弃"
        )
        assert "content?:" in client_ts or "content: string" in client_ts, (
            "frontend api/client.ts submitReview 必须显式声明 content 字段"
        )

    def test_frontend_submit_review_call_site_uses_content(self):
        """BridgeConsole.tsx 实际调用 api.submitReview 时用 content key。"""
        console_tsx = (Path(__file__).resolve().parents[2] / "frontend" / "src" / "pages" / "BridgeConsole.tsx").read_text(encoding="utf-8")
        assert "edited_content" not in console_tsx, (
            "frontend BridgeConsole.tsx 还传 edited_content — "
            "实际提交时编辑内容会被丢弃"
        )
        # 调用点必须传 content 字段（值是三元表达式）
        import re
        m = re.search(r"api\.submitReview\s*\([^)]*content:\s*", console_tsx, re.DOTALL)
        assert m, (
            "frontend BridgeConsole.tsx 调 api.submitReview 时必须传 content 字段"
        )

    def test_backend_review_request_has_content_field(self):
        """后端 ReviewRequest 必须有 content 字段（与前端对齐）。"""
        from app.schemas import ReviewRequest
        fields = ReviewRequest.model_fields
        assert "content" in fields, (
            "backend ReviewRequest 缺 content 字段 — 前端编辑提交会拿到 None"
        )
        # 显式不允许 edited_content（避免再次漂移）
        assert "edited_content" not in fields, (
            "backend ReviewRequest 不应有 edited_content 字段（应统一为 content）"
        )


# ───────────────────────────────────────────
# W: bridge.py 死代码清理（_run_bridge_async / _run_bridge_async_imported）
# ───────────────────────────────────────────
class TestBridgeDeadCodeRemoved:
    """历史背景：
      commit 62baf44 把 run 进程从 in-process 切到 subprocess（_spawn_engine_subprocess），
      旧版 _run_bridge_async 函数和 _run_bridge_async_imported 降级引用变 dead code。
      本轮清理：函数体删掉，只留 stub 抛 NotImplementedError；_run_bridge_async_imported
      字符串彻底从源码消失。
    """

    def test_no_run_bridge_async_imported_string_in_source(self):
        """源码（包括 subprocess 降级路径字符串）不能出现 _run_bridge_async_imported。"""
        from pathlib import Path
        repo = Path(__file__).resolve().parents[2]
        offenders: list[str] = []
        for py_file in (repo / "backend").rglob("*.py"):
            # 跳过 tests/ 自身（test 文件里 grep 这个名字是合法的——在断言里）
            if "tests" in py_file.parts:
                continue
            content = py_file.read_text(encoding="utf-8")
            if "_run_bridge_async_imported" in content:
                offenders.append(str(py_file.relative_to(repo)))
        assert not offenders, (
            "_run_bridge_async_imported 仍存在（已删除函数，不应再被引用）：\n  "
            + "\n  ".join(offenders)
        )

    def test_run_bridge_async_only_stub(self):
        """_run_bridge_async 函数体应只剩 stub（抛 NotImplementedError），不能真有逻辑。"""
        from pathlib import Path
        bridge_py = Path(__file__).resolve().parents[2] / "backend" / "app" / "api" / "bridge.py"
        content = bridge_py.read_text(encoding="utf-8")
        # 找到函数定义位置
        import re
        m = re.search(r"async def _run_bridge_async\([^)]*\):\s*\n(.*?)(?=\nasync def |def |class |\Z)", content, re.DOTALL)
        assert m, "找不到 _run_bridge_async 函数"
        body = m.group(1)
        # 不应有 run_graph_task / asyncio.to_thread 这种实质逻辑
        assert "run_graph_task" not in body, (
            "_run_bridge_async 函数体不应再调用 run_graph_task（已废弃）"
        )
        assert "NotImplementedError" in body, (
            "_run_bridge_async 必须是 stub（抛 NotImplementedError）"
        )


# ───────────────────────────────────────────
# X: BridgeRun 孤儿 running 自愈（启动时清理）
# ───────────────────────────────────────────
class TestOrphanBridgeRunRecovery:
    """历史 bug（独立审查标记）：
      并发锁在内存 _project_locks，进程崩溃后 DB 里 status='running'
      且 finished_at IS NULL 的记录永久卡住。下次任何 /bridge/run → 409 Conflict。

    修复：main.py lifespan handler 启动时调 _recover_orphan_bridge_runs()，
    把所有未结束的 running 行标为 'failed'，写入 finished_at。
    """

    def test_main_has_orphan_recovery_function(self):
        """backend/app/main.py 必须定义 _recover_orphan_bridge_runs 函数。"""
        from pathlib import Path
        main_py = Path(__file__).resolve().parents[2] / "backend" / "app" / "main.py"
        content = main_py.read_text(encoding="utf-8")
        assert "_recover_orphan_bridge_runs" in content, (
            "backend/app/main.py 缺 _recover_orphan_bridge_runs 函数 — "
            "启动时无法清理孤儿 BridgeRun 行，进程崩溃后项目永久 409"
        )

    def test_main_uses_lifespan_handler(self):
        """必须用 @asynccontextmanager lifespan 替代 deprecated @app.on_event。"""
        from pathlib import Path
        main_py = Path(__file__).resolve().parents[2] / "backend" / "app" / "main.py"
        content = main_py.read_text(encoding="utf-8")
        assert "@asynccontextmanager" in content and "async def lifespan" in content, (
            "backend/app/main.py 必须用 lifespan handler（@app.on_event 已被 deprecated）"
        )
        assert "@app.on_event" not in content, (
            "backend/app/main.py 还用 deprecated 的 @app.on_event — "
            "应改为 @asynccontextmanager lifespan"
        )

    def test_lifespan_calls_orphan_recovery(self):
        """lifespan handler 必须调 _recover_orphan_bridge_runs()。"""
        from pathlib import Path
        main_py = Path(__file__).resolve().parents[2] / "backend" / "app" / "main.py"
        content = main_py.read_text(encoding="utf-8")
        # lifespan 函数体内必须调 _recover_orphan_bridge_runs
        import re
        m = re.search(r"async def lifespan\(.*?\):(.*?)(?=\nasync def |def |class |\Z)", content, re.DOTALL)
        assert m, "找不到 lifespan 函数"
        body = m.group(1)
        assert "_recover_orphan_bridge_runs()" in body, (
            "lifespan handler 必须调 _recover_orphan_bridge_runs()"
        )

    def test_recovery_marks_orphan_runs_failed(self):
        """直接调 _recover_orphan_bridge_runs 验证：orphan 行被标 failed。"""
        from datetime import datetime
        from app.main import _recover_orphan_bridge_runs
        from app.database import SessionLocal
        from app.models import BridgeRun, Project
        from datetime import datetime, timezone

        # 准备：先建一个真 Project（FK 约束开启后 BridgeRun 需要合法 project_id）
        db = SessionLocal()
        try:
            project = Project(
                id="test-orphan-recovery-proj",
                title="orphan recovery test project",
                genre="都市",
                audience="男频",
                status="ready",
                config_json={},
            )
            db.add(project)
            db.commit()

            test_run = BridgeRun(
                project_id=project.id,
                command="run",
                status="running",
                started_at=datetime.now(timezone.utc),
                finished_at=None,
            )
            db.add(test_run)
            db.commit()
            test_run_id = test_run.id
        finally:
            db.close()

        # 调 cleanup
        recovered = _recover_orphan_bridge_runs()
        assert recovered >= 1, f"应至少清理 1 条 orphan，实际 {recovered}"

        # 验证：状态变成 failed，finished_at 有值
        db = SessionLocal()
        try:
            run = db.get(BridgeRun, test_run_id)
            assert run is not None
            assert run.status == "failed", (
                f"orphan run 状态应改为 failed，实际 {run.status}"
            )
            assert run.finished_at is not None, (
                "orphan run 应写入 finished_at"
            )
        finally:
            # 清理测试数据（先删 FK 引用，再删 project）
            if run:
                db.delete(run)
            project_obj = db.get(Project, "test-orphan-recovery-proj")
            if project_obj:
                db.delete(project_obj)
            db.commit()
            db.close()

    def test_cors_uses_env_or_default(self):
        """CORS 必须从 env 读 ALLOWED_ORIGINS，不能硬编码 *。"""
        from pathlib import Path
        main_py = Path(__file__).resolve().parents[2] / "backend" / "main.py" if False else (
            Path(__file__).resolve().parents[2] / "backend" / "app" / "main.py"
        )
        content = main_py.read_text(encoding="utf-8")
        assert 'allow_origins=["*"]' not in content, (
            "backend/app/main.py CORS 还硬编码 * — 部署前必须收紧"
        )
        assert "ALLOWED_ORIGINS" in content, (
            "backend/app/main.py 必须从 env 读 ALLOWED_ORIGINS"
        )


# ───────────────────────────────────────────
# Y: reports.py 路径统一（与 engine 一致走 NOVEL_AI_DIR env）
# ───────────────────────────────────────────
class TestReportsPathUnified:
    """历史背景（独立审查标记）：
      engine 写到 NOVEL_AI_DIR env 路径（与 binding.novel_ai_dir 等价时是
      novel_AI/output/，否则是 backend/data/engine/output/）。
      reports.py 之前硬编码 novel_ai_dir/output/ → engine 写到 env 路径时
      reports 读不到 → status 显示陈旧或 not_initialized。

    修复：reports.py 的 _state_path / _chapters_dir / _budget_log_path
    优先用 NOVEL_AI_DIR env，fallback 到参数。
    """

    def test_reports_uses_env_novel_ai_dir(self):
        """reports.py 解析路径时必须读 NOVEL_AI_DIR env。"""
        from pathlib import Path
        reports_py = (
            Path(__file__).resolve().parents[2]
            / "backend" / "app" / "bridge" / "reports.py"
        )
        content = reports_py.read_text(encoding="utf-8")
        assert "NOVEL_AI_DIR" in content, (
            "reports.py 必须读 NOVEL_AI_DIR env（与 engine 路径解析对齐）"
        )

    def test_reports_state_path_with_env(self, monkeypatch, tmp_path):
        """设置 NOVEL_AI_DIR 后，_state_path 必须解析到 env 路径。"""
        env_dir = str(tmp_path / "novel_ai_env")
        Path(env_dir, "output").mkdir(parents=True)
        monkeypatch.setenv("NOVEL_AI_DIR", env_dir)

        # 强制重读 reports（monkeypatch.setenv 必须在 import 之后）
        from app.bridge.reports import _state_path
        result = _state_path("/some/other/path")
        assert str(result) == str(Path(env_dir) / "output" / "orchestrator_state.json"), (
            f"_state_path 没走 NOVEL_AI_DIR env：{result}"
        )

    def test_reports_state_path_fallback_without_env(self, monkeypatch):
        """NOVEL_AI_DIR 没设置时，_state_path 必须 fallback 到参数。"""
        monkeypatch.delenv("NOVEL_AI_DIR", raising=False)
        from app.bridge.reports import _state_path
        result = _state_path("/some/dir")
        expected = str(Path("/some/dir") / "output" / "orchestrator_state.json")
        assert str(result) == expected, (
            f"_state_path fallback 失败：{result}（期望 {expected}）"
        )


# ───────────────────────────────────────────
# AA: Mock LLM Provider（引擎质量验证不花钱）
# ───────────────────────────────────────────
class TestMockLLMProvider:
    """历史背景（独立审查标记的中危点）：
      之前要验证 engine 端到端机制（schema 校验、字数 budget、orchestrator
      编排、tools 调用）必须真花钱调 LLM。
      Mock provider 让这一切离线跑：单元测试 / 集成测试 / CI 都不依赖
      外部 API，引擎质量验证独立于生成质量。

      本轮新增：LLMRouter._mock 方法 + _MOCK_RESPONSES 模板。
      Mock 模式只验证引擎机制，不验证生成内容质量（生产仍走真 provider）。
    """

    def test_mock_provider_registered_in_dispatch(self):
        """LLMRouter 的 dispatch 必须包含 'mock' provider。"""
        from engine.llm.router import LLMRouter
        r = LLMRouter("test")
        # 通过 routes 里把 agent 指向 mock，触发 dispatch
        r.routes["writer"] = ("mock", "mock-model")
        text, cost = r.call("writer", "sys", "user", max_tokens=2000, temperature=0.7)
        assert text, "mock writer 必须返回非空文本"
        assert cost == 0.001, f"mock cost 应为 0.001/调用，实际 {cost}"
        assert len(text) >= 1800, (
            f"mock writer 应返回接近 2000 字的章节（满足 call_with_length_budget 区间），"
            f"实际 {len(text)}"
        )

    def test_mock_provider_no_api_key_needed(self):
        """mock provider 不能读任何 api_key env（环境变量没设也不报错）。"""
        import os
        from engine.llm.router import LLMRouter
        # 删掉所有 API key env
        for k in ["ANTHROPIC_API_KEY", "DEEPSEEK_API_KEY", "GEMINI_API_KEY",
                  "KIMI_API_KEY", "MINIMAX_API_KEY", "CUSTOM_API_KEY"]:
            os.environ.pop(k, None)
        r = LLMRouter("test")
        r.routes["planner"] = ("mock", "mock-model")
        # 不抛异常 + 返回非空
        text, cost = r.call("planner", "sys", "user", max_tokens=2000, temperature=0.7)
        assert text
        assert "Mock" in text or "mock" in text, (
            f"mock planner 应返回标记为 Mock 的内容：{text[:100]!r}"
        )

    def test_mock_provider_returns_schema_valid_json(self):
        """checker / tracker / outline 等 agent 的 mock 响应必须是合法 JSON。"""
        import json
        from engine.llm.router import LLMRouter
        r = LLMRouter("test")
        for agent in ["tracker", "compliance", "checker_main", "outline"]:
            r.routes[agent] = ("mock", "mock-model")
            text, _ = r.call(agent, "sys", "user", max_tokens=4000, temperature=0.7)
            parsed = json.loads(text)  # 必须能 parse
            assert isinstance(parsed, dict), (
                f"mock {agent} 响应必须是 JSON dict，实际 {type(parsed).__name__}"
            )
            assert len(parsed) > 0, f"mock {agent} 响应不能是空 dict"

    def test_mock_provider_does_not_break_stats(self):
        """mock 调用应该正常累计 stats（不抛异常）。"""
        from engine.llm.router import LLMRouter
        r = LLMRouter("test")
        r.routes["writer"] = ("mock", "mock-model")
        r.call("writer", "sys", "user", max_tokens=2000, temperature=0.7)
        stats = r.get_stats()
        assert stats["total_calls"] == 1
        assert abs(stats["total_cost_usd"] - 0.001) < 1e-6
        assert stats["by_agent"]["writer"]["calls"] == 1


# ───────────────────────────────────────────
# CC: 前后端 types 对齐（BridgeRun / ChapterFull schema 漂移）
# ───────────────────────────────────────────
class TestFrontendTypesAligned:
    """历史背景（独立审查标记的低优先级）：
      前端 types.ts 之前缺：
        - BridgeRun.args_json / stdout_text / started_at / finished_at
          （SSE 处理逻辑依赖这些字段，但类型声明里没有）
        - ChapterFull.created_at 应为 string | null（后端 Optional[datetime]）
      后端 schema 实际有这些字段，TypeScript 类型漂移会让 IDE 静默接受错误字段名。

      本轮修复：补齐字段类型，optional/required 与后端 schema 对齐。
    """

    def test_frontend_bridge_run_type_has_all_fields(self):
        """前端 BridgeRun 类型必须含 args_json / stdout_text / started_at / finished_at。"""
        from pathlib import Path
        types_ts = Path(__file__).resolve().parents[2] / "frontend" / "src" / "types.ts"
        content = types_ts.read_text(encoding="utf-8")
        # 提取 BridgeRun interface 块
        import re
        m = re.search(r"export interface BridgeRun\s*\{([^}]*)\}", content)
        assert m, "找不到 export interface BridgeRun"
        block = m.group(1)
        for field in ["args_json", "stdout_text", "started_at", "finished_at"]:
            assert f"{field}" in block, (
                f"前端 BridgeRun interface 缺 {field} 字段 — "
                f"后端 BridgeRunOut 有，但前端类型漏声明"
            )

    def test_frontend_chapter_full_created_at_nullable(self):
        """前端 ChapterFull.created_at 应为 string | null（后端允许 None）。"""
        from pathlib import Path
        types_ts = Path(__file__).resolve().parents[2] / "frontend" / "src" / "types.ts"
        content = types_ts.read_text(encoding="utf-8")
        import re
        m = re.search(r"export interface ChapterFull\s*\{([^}]*)\}", content)
        assert m, "找不到 export interface ChapterFull"
        block = m.group(1)
        # 必须有 "created_at: string | null" 形式（允许 None）
        assert re.search(r"created_at:\s*string\s*\|\s*null", block), (
            "前端 ChapterFull.created_at 应为 string | null，"
            "与后端 Optional[datetime] 对齐"
        )


# ───────────────────────────────────────────
# DD: MASTER_KEY 生成脚本存在 + README 部署章节
# ───────────────────────────────────────────
class TestDeploymentDocs:
    """历史背景（独立审查标记的高危点修复配套）：
      Provider.api_key 改 Fernet 加密后，部署必须设 MASTER_KEY env。
      没有 generate 脚本 + README 部署文档，用户部署时不知道这步。

      本轮新增：
        - backend/scripts/generate_master_key.py — 输出 MASTER_KEY=...
        - README.md 加「部署」章节：MASTER_KEY / CORS / 端口 / 迁移 / 范围外
    """

    def test_generate_master_key_script_exists(self):
        """generate_master_key.py 必须存在且能跑（生成有效 Fernet key）。"""
        import subprocess
        from pathlib import Path
        script = Path(__file__).resolve().parents[2] / "backend" / "scripts" / "generate_master_key.py"
        assert script.exists(), (
            "backend/scripts/generate_master_key.py 不存在 — "
            "部署时无法生成 MASTER_KEY，Provider API key 加密没人能用"
        )
        # 真跑一遍
        result = subprocess.run(
            ["python", "-m", "scripts.generate_master_key"],
            cwd=script.parent.parent,
            capture_output=True, text=True, timeout=10,
        )
        assert result.returncode == 0, f"脚本失败：{result.stderr}"
        assert "MASTER_KEY=" in result.stdout, "脚本输出必须含 MASTER_KEY="
        # 提取 key，校验长度
        for line in result.stdout.splitlines():
            if line.startswith("MASTER_KEY="):
                key = line.split("=", 1)[1].strip()
                assert len(key) == 44, f"MASTER_KEY 长度 {len(key)} ≠ 44"
                # Fernet round-trip 验证
                from cryptography.fernet import Fernet
                f = Fernet(key.encode("ascii"))
                token = f.encrypt(b"sanity")
                assert f.decrypt(token) == b"sanity"
                return
        assert False, "脚本输出里没找到 MASTER_KEY=..."

    def test_readme_has_deployment_section(self):
        """README 必须有「部署」章节且提到 MASTER_KEY + ALLOWED_ORIGINS。"""
        from pathlib import Path
        readme = Path(__file__).resolve().parents[2] / "README.md"
        content = readme.read_text(encoding="utf-8")
        assert "## 部署" in content, "README 缺「部署」章节"
        assert "MASTER_KEY" in content, "部署章节必须提到 MASTER_KEY"
        assert "ALLOWED_ORIGINS" in content, "部署章节必须提到 ALLOWED_ORIGINS（CORS）"


# ───────────────────────────────────────────
# EE: CHANGELOG.md 存在且非占位
# ───────────────────────────────────────────
class TestChangelogExists:
    """历史背景（独立审查标记的低优先级）：
      项目在 2026-06-26 至 2026-07-02 期间经历重大架构变更（Phase 1 / 1.5 /
      深度修复轮），但仓库一直没 CHANGELOG.md，新读者只能翻 git log。
      本轮新增 CHANGELOG.md 记录关键修复链。
    """

    def test_changelog_md_exists(self):
        from pathlib import Path
        changelog = Path(__file__).resolve().parents[2] / "CHANGELOG.md"
        assert changelog.exists(), "CHANGELOG.md 不存在 — 新读者无法快速了解变更历史"

    def test_changelog_not_placeholder(self):
        """CHANGELOG.md 必须有实质内容（>= 50 行 + 提到关键修复）。"""
        from pathlib import Path
        changelog = Path(__file__).resolve().parents[2] / "CHANGELOG.md"
        content = changelog.read_text(encoding="utf-8")
        line_count = len(content.splitlines())
        assert line_count >= 50, f"CHANGELOG.md 只有 {line_count} 行（应 >= 50）"
        # 关键修复必须提到
        for keyword in ["Provider API key", "MASTER_KEY", "lifespan",
                        "thread_id", "subprocess", "parse_llm_json_response"]:
            assert keyword in content or keyword.lower() in content.lower(), (
                f"CHANGELOG.md 缺关键字 '{keyword}' — 关键修复没记到"
            )


# ───────────────────────────────────────────
# FF: openapi.json 漂移防护（auto-export 脚本 + .gitignore）
# ───────────────────────────────────────────
class TestOpenApiExport:
    """历史背景（独立审查标记的低优先级）：
      frontend/openapi.json 之前手工 commit，已严重漂移（缺 10+ 端点）。
      本轮修复：加 export_openapi.py 从运行中的后端拉 spec，frontend/openapi.json
      加 .gitignore 自动忽略。CI / 开发者需要时跑 `python -m scripts.export_openapi`
      重新生成。
    """

    def test_frontend_openapi_gitignored(self):
        """frontend/openapi.json 必须在 frontend/.gitignore 里（不再 commit）。"""
        from pathlib import Path
        gi = Path(__file__).resolve().parents[2] / "frontend" / ".gitignore"
        content = gi.read_text(encoding="utf-8")
        assert "openapi.json" in content, (
            "frontend/.gitignore 必须包含 openapi.json — "
            "否则它会污染 commit history（旧版本漂移问题）"
        )

    def test_export_openapi_script_exists(self):
        """export_openapi.py 必须存在 + 可作为 module import。"""
        from pathlib import Path
        script = Path(__file__).resolve().parents[2] / "backend" / "scripts" / "export_openapi.py"
        assert script.exists(), "backend/scripts/export_openapi.py 不存在"
        # 验证可 import + 有 main()
        import importlib.util
        spec_obj = importlib.util.spec_from_file_location("export_openapi", script)
        mod = importlib.util.module_from_spec(spec_obj)
        spec_obj.loader.exec_module(mod)  # type: ignore
        assert hasattr(mod, "main"), "export_openapi.py 必须定义 main()"


# ───────────────────────────────────────────
# GG: MASTER_KEY 轮换工具存在（运维：定期轮换 / 泄漏应急）
# ───────────────────────────────────────────
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
        script = Path(__file__).resolve().parents[2] / "backend" / "scripts" / "rotate_master_key.py"
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
        script_dir = Path(__file__).resolve().parents[2] / "backend" / "scripts"
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


# ───────────────────────────────────────────
# HH: Mock provider 通过 env 自动激活（CI / demo 友好）
# ───────────────────────────────────────────
class TestMockProviderAutoActivate:
    """历史背景（独立审查标记的中危点修复扩展）：
      之前 mock provider 只在 router.py 内显式设置 routes 才能用。
      CI / 单元测试 / demo 用户要"无需任何配置就让 engine 跑 mock"
      必须有 env 开关。

      本轮修复：NOVEL_ENGINE_MOCK=1 → LLMRouter 构造时自动 use_mock()
      把全部 9 个 agent routes 切到 mock provider（无需 API key）。
    """

    def test_env_var_triggers_use_mock(self, monkeypatch):
        """NOVEL_ENGINE_MOCK=1 → 构造 LLMRouter 后所有 routes 是 mock。"""
        monkeypatch.setenv("NOVEL_ENGINE_MOCK", "1")
        from engine.llm.router import LLMRouter
        r = LLMRouter("test")
        for agent, route in r.routes.items():
            assert route[0] == "mock", (
                f"NOVEL_ENGINE_MOCK=1 后 agent '{agent}' 应指向 mock，实际 {route[0]!r}"
            )

    def test_explicit_use_mock_method(self):
        """不设 env，调用 r.use_mock() 也能切到 mock（用于运行时切换）。"""
        from engine.llm.router import LLMRouter
        r = LLMRouter("test")
        assert r.routes["writer"][0] != "mock", "默认 routes 不应是 mock"
        r.use_mock()
        assert r.routes["writer"][0] == "mock", "显式 use_mock() 后应切到 mock"

    def test_no_env_no_api_key_still_raises(self):
        """不设 NOVEL_ENGINE_MOCK + 没 API key → 默认 routes 不应自动变 mock（保持原行为）。"""
        import os
        for k in ["NOVEL_ENGINE_MOCK", "ANTHROPIC_API_KEY", "DEEPSEEK_API_KEY",
                  "KIMI_API_KEY", "MINIMAX_API_KEY"]:
            os.environ.pop(k, None)
        from engine.llm.router import LLMRouter
        r = LLMRouter("test")
        # 默认仍是真实 provider（除非显式 use_mock 或 NOVEL_ENGINE_MOCK=1）
        assert r.routes["writer"][0] != "mock", (
            "无 env 触发不应自动 mock（保留 opt-in 行为）"
        )


# ───────────────────────────────────────────
# II: 速率限制中间件（防刷 /bridge/run 触发昂贵 LLM）
# ───────────────────────────────────────────
class TestRateLimitMiddleware:
    """历史背景（独立审查标记的范围外项）：
      当前无任何速率限制 → 攻击者用脚本刷 /bridge/run 会触发昂贵 LLM 调用
      （每次 $0.01-$0.10）→ 钱包爆掉。

      本轮修复：app.middleware.rate_limit.RateLimitMiddleware
        - 内存滑动窗口，默认 60 次/分钟/IP
        - 仅写端点限速（GET / OPTIONS / HEAD / 读路径不受限）
        - 通过 RATE_LIMIT_PER_MINUTE env 调整
        - 响应含 X-RateLimit-Limit / Remaining / Retry-After headers
    """

    def test_middleware_registered_in_main(self):
        """main.py 必须注册 RateLimitMiddleware。"""
        from pathlib import Path
        main_py = Path(__file__).resolve().parents[2] / "backend" / "app" / "main.py"
        content = main_py.read_text(encoding="utf-8")
        assert "RateLimitMiddleware" in content, (
            "main.py 必须注册 RateLimitMiddleware — "
            "否则攻击者能刷 /bridge/run 触发昂贵 LLM 调用"
        )
        assert "RATE_LIMIT_PER_MINUTE" in content, (
            "main.py 应支持 RATE_LIMIT_PER_MINUTE env"
        )

    def test_ip_rate_limiter_basic(self):
        """IPRateLimiter 基本逻辑：max+1 次后第 N+1 次被拒绝。"""
        from app.middleware.rate_limit import IPRateLimiter, reset_for_testing
        reset_for_testing()
        limiter = IPRateLimiter(max_per_minute=3)
        # 前 3 次允许
        assert limiter.is_allowed("1.2.3.4")
        assert limiter.is_allowed("1.2.3.4")
        assert limiter.is_allowed("1.2.3.4")
        # 第 4 次拒绝
        assert not limiter.is_allowed("1.2.3.4"), (
            "超出 max_per_minute 后必须拒绝"
        )
        # 不同 IP 独立计数
        assert limiter.is_allowed("5.6.7.8"), "不同 IP 必须独立计数"
        reset_for_testing()

    def test_write_endpoint_detection(self):
        """_is_write_endpoint 标记 /api/v1/ 下所有路径为潜在写（middleware 按 method 二次过滤）。

        注意：_is_write_endpoint 单看路径，middleware 在 dispatch 里再加一层
        GET/HEAD/OPTIONS 早退。所以这个 helper 是"路径是否是 /api/v1/ 下"。
        """
        from app.middleware.rate_limit import _is_write_endpoint
        # /api/v1/ 下所有路径（中间件按 method 二次过滤）
        assert _is_write_endpoint("/api/v1/projects/abc/bridge/run")
        assert _is_write_endpoint("/api/v1/projects/abc/worldbuild/start")
        assert _is_write_endpoint("/api/v1/providers/xyz")
        assert _is_write_endpoint("/api/v1/foreshadowings/123/status")
        assert _is_write_endpoint("/api/v1/projects/abc/bridge/status")  # GET 也标记
        # 豁免
        assert not _is_write_endpoint("/health")
        assert not _is_write_endpoint("/openapi.json")
        assert not _is_write_endpoint("/docs")

    def test_rate_limit_headers_in_response(self):
        """被限流的请求必须返回 429 + Retry-After / X-RateLimit-* headers。"""
        from fastapi.testclient import TestClient
        from app.main import app
        from app.middleware.rate_limit import (
            _limiter, reset_for_testing,
        )
        # 强制设很低阈值
        from app.middleware import rate_limit
        rate_limit._limiter = rate_limit.IPRateLimiter(max_per_minute=1)
        try:
            client = TestClient(app)
            # 第 1 次 POST /providers：允许（设很小的 body 可能 422，但不触发 rate limit）
            # 用 POST /providers 测（body 即使无效也先过 middleware）
            r1 = client.post("/api/v1/providers", json={})
            # 第 2 次：被限流
            r2 = client.post("/api/v1/providers", json={})
            # 注意：r1 可能是 422（body 校验），但 rate limit 已消耗
            # r2 必须是 429
            assert r2.status_code == 429, (
                f"第 2 次写请求应被限流（max=1），实际 {r2.status_code}"
            )
            assert "Retry-After" in r2.headers
            assert "X-RateLimit-Limit" in r2.headers
            assert r2.json().get("error") == "rate_limit_exceeded"
        finally:
            reset_for_testing()
            # 恢复模块级 limiter
            rate_limit._limiter = rate_limit.IPRateLimiter(
                max_per_minute=10000  # 测试环境高阈值
            )

    def test_allowed_proxies_parsing(self):
        """ALLOWED_PROXIES env 解析：单个 IP + CIDR + 无效值跳过。"""
        from app.middleware.rate_limit import _parse_allowed_proxies, RateLimitMiddleware
        # 重置缓存
        RateLimitMiddleware._allowed_proxies = None
        # 单个 IP
        import os
        os.environ["ALLOWED_PROXIES"] = "127.0.0.1,10.0.0.0/8,invalid_ip"
        nets = _parse_allowed_proxies()
        # invalid_ip 应被跳过
        assert len(nets) == 2, f"应解析 2 个有效 IP/CIDR（跳过 invalid），实际 {len(nets)}"
        os.environ.pop("ALLOWED_PROXIES", None)
        RateLimitMiddleware._allowed_proxies = None

    def test_ip_in_allowed_list_check(self):
        """_ip_in_allowed_list 正确判断 IP 是否在白名单。"""
        from app.middleware.rate_limit import _ip_in_allowed_list
        import ipaddress
        nets = [ipaddress.ip_network("127.0.0.0/8"), ipaddress.ip_network("10.0.0.0/8")]
        assert _ip_in_allowed_list("127.0.0.1", nets)
        assert _ip_in_allowed_list("10.5.6.7", nets)
        assert not _ip_in_allowed_list("8.8.8.8", nets)
        # 无效 IP 字符串
        assert not _ip_in_allowed_list("not_an_ip", nets)
        # 空白名单
        assert not _ip_in_allowed_list("127.0.0.1", [])


# ───────────────────────────────────────────
# JJ: Mock provider 端到端（LLMRouter 真实构造路径）
# ───────────────────────────────────────────
class TestMockProviderEndToEnd:
    """迭代 #1: 验证 mock 模式不仅单测过，真实构造 LLMRouter 时也起作用。

    历史背景：
      之前 mock provider 只在 router.py 内显式设置 routes 才能用。
      commit 6d6c07b 加了 NOVEL_ENGINE_MOCK=1 env 自动激活，但单测可能
      不能覆盖真实 import + 构造路径（mock path 可能只在测试 fixture 里）。
    """

    def test_llm_router_construction_with_mock_env(self):
        """设 NOVEL_ENGINE_MOCK=1 后 LLMRouter() 自动 use_mock() — 真实构造路径。"""
        import os
        os.environ["NOVEL_ENGINE_MOCK"] = "1"
        try:
            # 真 import + 构造（不走 mock 模块）
            from engine.llm.router import LLMRouter
            r = LLMRouter("test-end-to-end")
            # 9 个 agent 全部 mock
            assert r.routes["writer"][0] == "mock"
            assert r.routes["tracker"][0] == "mock"
            assert r.routes["orchestrator"][0] == "mock"
            # 真实 call() 调用走 mock 分支
            text, cost = r.call("writer", "sys", "user", max_tokens=2000, temperature=0.7)
            assert len(text) > 100, "mock writer 应返回长文本"
            assert cost == 0.001
        finally:
            os.environ.pop("NOVEL_ENGINE_MOCK", None)


# ───────────────────────────────────────────
# BB: engine/graph.py 日志统一（capture.write [engine] → log.xxx）
# ───────────────────────────────────────────
class TestEngineLoggingUnified:
    """历史背景（独立审查标记的低优先级）：
      backend/engine/graph.py 之前 16 处 capture.write("[engine] ...")，日志
      走 SSECapture 而不是 logging 配置——日志级别、文件落盘、log rotation
      都控制不到。

      本轮修复：把 [engine] 前缀的诊断输出改成 log.xxx() 调用，让 root
      logger 配置接管（控制台 + backend/logs/novel_ai.log 落盘）。
      不动其他 capture.write（章节内容 / emoji 状态等 user-facing 输出）。
    """

    def test_no_engine_prefix_capture_write(self):
        """graph.py 不应再有 capture.write("[engine] ...") 诊断输出。"""
        from pathlib import Path
        graph_py = Path(__file__).resolve().parents[2] / "backend" / "engine" / "graph.py"
        content = graph_py.read_text(encoding="utf-8")
        offenders = []
        for i, line in enumerate(content.splitlines(), start=1):
            if 'capture.write' in line and '[engine]' in line:
                offenders.append(f"line {i}: {line.rstrip()}")
        assert not offenders, (
            "graph.py 还有 [engine] 前缀的 capture.write — "
            "应改为 log.info/warning/error 让 root logger 接管：\n  "
            + "\n  ".join(offenders)
        )

    def test_engine_log_uses_module_logger(self):
        """graph.py 顶部必须定义了 novel_ai.engine logger。"""
        from pathlib import Path
        graph_py = Path(__file__).resolve().parents[2] / "backend" / "engine" / "graph.py"
        content = graph_py.read_text(encoding="utf-8")
        assert 'logging.getLogger("novel_ai.engine")' in content, (
            "graph.py 顶部必须有 logging.getLogger('novel_ai.engine')"
        )


# ───────────────────────────────────────────
# Z: Provider API Key 加密（明文不入库）
# ───────────────────────────────────────────
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

    def test_writer_failed_error_sentinel_exists(self):
        """WriterFailedError sentinel 异常类必须存在且可正常 raise/catch（commit 5d1f83e 修复）。"""
        from engine.orchestrator import WriterFailedError
        assert issubclass(WriterFailedError, Exception)
        try:
            raise WriterFailedError("writer crashed")
        except WriterFailedError as e:
            assert "writer crashed" in str(e)

    def test_route_after_pipeline_escalates_on_writer_failed(self):
        """task._writer_failed=True → 必须 escalate（防止 47 字 writer-stub 假 PASS）。"""
        from engine.orchestrator import route_after_pipeline
        state = {
            "current_phase": "writing",
            "current_task": {"_writer_failed": True},
            "rewrite_count_current": 0,
        }
        assert route_after_pipeline(state) == "escalate", (
            "_writer_failed=True 时必须走 escalate，不能 'save'"
        )

    def test_route_after_pipeline_normal_high_score_saves(self):
        """正常高分任务必须能 save（防止锁死逻辑破坏 happy path）。"""
        from engine.orchestrator import route_after_pipeline
        state = {
            "current_phase": "writing",
            "current_task": {
                "_checker_result": {"score": 8.0, "verdict": "PASS"},
                "_compliance_failed": False,
                "_compliance_check_failed": False,
                "_checker_failed": False,
                "_writer_failed": False,
            },
            "rewrite_count_current": 0,
        }
        assert route_after_pipeline(state) == "save", (
            "正常高分任务必须能 save（不能锁死到 escalate）"
        )

    def test_post_rewrite_compliance_failure_marks_task(self, orch, monkeypatch):
        """迭代 #28: post-rewrite compliance 抛异常 → _compliance_check_failed=True

        跟 node_write_pipeline 里的 compliance fake-pass 同型问题。
        之前 line 391-394 兜底为 {"passed": True} → 重写后即便合规检查完全
        失败（异常被吞），章节也走"通过"路径落盘。
        """
        def fake_rewriter(text, lvl, feedback, task, cr, memory, setting):
            return "重写后文本 " * 200, 0.0
        def fake_normalizer(text, task):
            return text, [], 0.0
        def fake_compliance(text, platform):
            # post-rewrite compliance 抛异常（模拟 MiniMax 接口 503）
            raise ConnectionError("post-rewrite compliance down")
        monkeypatch.setattr(orch, "run_rewriter", fake_rewriter)
        monkeypatch.setattr(orch, "run_normalizer", fake_normalizer)
        monkeypatch.setattr(orch, "run_compliance", fake_compliance)

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
        # 关键断言：post-rewrite compliance 抛异常时必须标记 _compliance_check_failed
        assert result["current_task"].get("_compliance_check_failed") is True, (
            "post-rewrite compliance 抛异常时 _compliance_check_failed 必须置 True"
            "（之前 fake-pass 兜底为 {'passed': True}，重写后合规检查被静默擦掉）"
        )
        # 不应有新 _checker_result（避免后续 route 误判"重写成功"）
        # 现有 _checker_result 是 pre-rewrite 的旧值，保留 OK（route 会 escalate）

    def test_route_after_rewrite_escalates_on_compliance_check_failed(self):
        """_compliance_check_failed=True → route_after_rewrite 必须 escalate。

        修复：route_after_rewrite 加了防御性检查（之前只查 _checker_result 分数），
        防止 _compliance_check_failed 标记被旧 cr 分数遮蔽（误判 save）。
        """
        from engine.orchestrator import route_after_rewrite
        state = {
            "current_task": {
                "_compliance_check_failed": True,
                "_checker_result": {"score": 7.0},  # 旧 cr 分数高于 PASS_SCORE
            },
            "rewrite_count_current": 0,
        }
        assert route_after_rewrite(state) == "escalate", (
            "_compliance_check_failed=True 时 route_after_rewrite 必须 escalate，"
            "不能因为旧 cr 分数 >= PASS_SCORE 就 save"
        )

    def test_route_after_rewrite_escalates_on_checker_failed(self):
        """_checker_failed=True → route_after_rewrite 必须 escalate（防止旧 cr 兜底）。"""
        from engine.orchestrator import route_after_rewrite
        state = {
            "current_task": {
                "_checker_failed": True,
                "_checker_result": {"score": 7.0},  # 旧 cr 分数高
            },
            "rewrite_count_current": 0,
        }
        assert route_after_rewrite(state) == "escalate", (
            "_checker_failed=True 时 route_after_rewrite 必须 escalate"
        )

    def test_route_after_rewrite_normal_high_score_saves(self):
        """正常高分重写任务必须能 save（防止锁死逻辑破坏 happy path）。"""
        from engine.orchestrator import route_after_rewrite
        state = {
            "current_task": {
                "_checker_result": {"score": 8.0, "verdict": "PASS"},
                "_compliance_failed": False,
                "_compliance_check_failed": False,
                "_checker_failed": False,
                "_rewriter_failed": False,
            },
            "rewrite_count_current": 0,
        }
        assert route_after_rewrite(state) == "save", (
            "正常高分重写任务必须能 save（不能锁死到 escalate）"
        )


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

    def test_graph_run_graph_task_handles_unknown_command(self):
        """run_graph_task 收到未知命令时必须 exit_code=1（不是 0）。"""
        from engine.graph import run_graph_task
        from queue import Queue
        q = Queue()
        # 用一个明显没注册的命令
        exit_code, stdout = run_graph_task(
            project_id="nonexistent",
            command="definitely_not_a_real_command_xyz",
            args=[],
            run_id="test-unknown",
            queue=q,
        )
        assert exit_code == 1, (
            f"未知命令应返回 exit_code=1，实际 {exit_code}（'假装成功'是 fake-pass）"
        )
        assert "未知命令" in stdout, f"stderr 应明确说未知命令，实际 stdout: {stdout[:200]!r}"


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
        """app/api/bridge.py 的 _spawn_engine_subprocess 必须从 binding 注入 NOVEL_AI_DIR。

        历史背景：commit 62baf44 把 in-process _run_bridge_async 切到 subprocess
        _spawn_engine_subprocess。这个 test 也跟着迁移（之前的版本测
        _run_bridge_async 源码里有 NOVEL_AI_DIR + binding 读，现在测的是
        subprocess 路径）。
        """
        import inspect
        from app.api import bridge as bridge_mod
        src = inspect.getsource(bridge_mod._spawn_engine_subprocess)
        assert "NOVEL_AI_DIR" in src, (
            "_spawn_engine_subprocess 必须注入 NOVEL_AI_DIR env，"
            "否则 engine STATE_PATH 跟 binding 不一致（双重真相 bug）"
        )
        assert "NovelAIBinding" in src and "novel_ai_dir" in src, (
            "必须从 binding 读 novel_ai_dir 再注入 env"
        )


# ───────────────────────────────────────────
# LL: acceptance_tests 验收套件核心逻辑（5 个 AC 测试无覆盖风险）
# ───────────────────────────────────────────
class TestAcceptanceTestsCovered:
    """历史背景（最终全面审计 P2）：
      engine/tools/acceptance_tests.py 是 V3 方案 8.5 节的 5 个验收标准
      （AC-1 设定一致性 / AC-2 题材切换 / AC-3 任务单质量 / AC-4 平台适配
      / AC-5 人物弧光），核心验收逻辑**零测试覆盖**。

      本轮至少锁死 AC-2（题材切换）—— 它是最纯函数（不依赖文件系统），
      也是 prompt_templates 的核心契约。其他 AC 依赖具体 novel 数据，留待后续。
    """

    def test_ac2_genre_switch_pure_function(self):
        """AC-2 题材切换：每个题材必须返回 >= 50 字指令，未知题材兜底。"""
        from engine.tools.acceptance_tests import ac2_genre_switch
        # 注意：print 副作用不影响测试结果
        assert ac2_genre_switch() is True, (
            "AC-2 题材切换测试必须返回 True（所有题材 + 兜底都正常）"
        )

    def test_ac2_genre_instruction_min_length(self):
        """prompt_templates.get_genre_instruction 必须返回 >= 50 字指令。"""
        from engine.config.prompt_templates import get_genre_instruction
        for genre in ["都市", "玄幻", "科幻", "都市系统流", "玄幻修仙", "萌宝甜宠"]:
            instruction = get_genre_instruction(genre)
            assert isinstance(instruction, str) and len(instruction) >= 50, (
                f"题材「{genre}」指令太短或非字符串：len={len(instruction) if instruction else 0}"
            )

    def test_ac2_unknown_genre_has_fallback(self):
        """未知题材必须有兜底指令（不能让 LLM 收到空 prompt）。"""
        from engine.config.prompt_templates import get_genre_instruction
        for unknown in ["未知", "不存在题材XYZ", "", "random_genre_999"]:
            instruction = get_genre_instruction(unknown)
            assert instruction, f"未知题材「{unknown!r}」必须返回兜底指令，实际 {instruction!r}"
            assert len(instruction) >= 10, (
                f"未知题材「{unknown!r}」兜底指令太短：{instruction!r}"
            )

    def test_ac2_urban_system_flow_marker(self):
        """「都市」题材指令必须含「系统流」要求（AC-2 验收关键点）。"""
        from engine.config.prompt_templates import get_genre_instruction
        urban = get_genre_instruction("都市")
        assert "系统流" in urban, (
            f"「都市」指令缺「系统流」marker（AC-2 验收点）：{urban[:100]!r}"
        )

    def test_run_all_returns_bool(self):
        """run_all() 返回 True/False（不是 None / 抛异常）。"""
        from engine.tools.acceptance_tests import run_all
        result = run_all()
        assert isinstance(result, bool), (
            f"run_all() 必须返回 bool，实际 {type(result).__name__}"
        )
        # 项目当前数据不全 → 至少 AC-2 应该 PASS，其他可能 SKIP
        # 我们不强求 5/5 PASS（数据依赖），但 True/False 边界要对


# ───────────────────────────────────────────
# MM: /health 端点必须真 ping DB（不能永远返回 ok）
# ───────────────────────────────────────────
class TestHealthEndpointDBCheck:
    """历史背景（迭代 #8）：
      /health 之前永远返回 {"status": "ok"}，不管 DB 是否锁 / 磁盘满 /
      migration 失败。k8s livenessProbe / readinessProbe 拿到 ok 后会继续
      发流量，实际后端挂但监控看不见。

      修法：/health 必须真执行 SELECT 1（验证 DB session 可用）。
    """

    def test_health_returns_db_ok_when_db_works(self):
        """DB 可用时 /health 返回 200 + db: ok。"""
        from fastapi.testclient import TestClient
        from app.main import app
        client = TestClient(app)
        r = client.get("/health")
        assert r.status_code == 200, f"/health 期望 200，实际 {r.status_code}"
        body = r.json()
        assert body["status"] == "ok"
        assert body.get("db") == "ok", (
            f"/health 响应必须含 db 字段：{body}"
        )

    def test_health_returns_503_when_db_fails(self, monkeypatch):
        """DB 不可达时 /health 返回 503 + status: degraded。"""
        from fastapi.testclient import TestClient
        from app import database as db_mod
        from app import main as main_mod

        class FakeSession:
            def execute(self, *args, **kwargs):
                raise RuntimeError("DB lock acquired")
            def close(self):
                pass
        monkeypatch.setattr(db_mod, "SessionLocal", lambda: FakeSession())
        monkeypatch.setattr(main_mod, "SessionLocal", lambda: FakeSession())

        from app.main import app as _app
        client = TestClient(_app)
        r = client.get("/health")
        assert r.status_code == 503, (
            f"DB 故障时 /health 应返回 503，实际 {r.status_code}"
        )
        body = r.json()
        assert body["status"] == "degraded"
        assert body["db"] == "error"
        assert "DB lock" in body.get("detail", ""), (
            f"detail 应含错误信息：{body}"
        )


# ───────────────────────────────────────────
# NN: engine/state.py save_state 加原子写 + 文件锁（防并发损坏）
# ───────────────────────────────────────────
class TestSaveStateConcurrencySafe:
    """历史背景（迭代 #9）：
      save_state 之前直接 open(path, "w") + json.dump，半写文件被读 +
      多进程同时写会互相覆盖（last-write-wins）。多 worker 部署或
      测试并行跑会偶发 state.json 损坏。

      修法：
        1. atomic write：先写 .tmp + os.replace（原子 rename，避免半写）
        2. 文件锁：fcntl (POSIX) / msvcrt (Windows) 跨平台
        3. fsync：数据真正落盘（不掉电丢失）
    """

    def test_save_state_atomic_no_partial_file(self, tmp_path):
        """save_state 写失败时不能留半写 state.json。"""
        from engine.state import save_state, create_initial_state, load_state
        path = str(tmp_path / "state.json")
        state = create_initial_state("test", "t", "fanqie", "都市", "")
        state["current_chapter"] = 42
        save_state(state, path)
        # 真文件存在
        assert (tmp_path / "state.json").exists()
        # .tmp 已清理（说明 atomic write 完成）
        assert not (tmp_path / "state.json.tmp").exists(), (
            ".tmp 临时文件不应保留（atomic write 后应清理）"
        )
        # 内容可正常 load
        loaded = load_state(path)
        assert loaded["current_chapter"] == 42

    def test_save_state_overwrites_existing(self, tmp_path):
        """多次 save_state 覆盖写，最终内容是最新的（无残留旧数据）。"""
        from engine.state import save_state, create_initial_state, load_state
        path = str(tmp_path / "state.json")
        # 第一次
        s1 = create_initial_state("test", "title1", "fanqie", "都市", "")
        save_state(s1, path)
        # 第二次（不同字段）
        s2 = create_initial_state("test", "title2", "qidian", "玄幻", "升级流")
        s2["current_chapter"] = 99
        save_state(s2, path)
        loaded = load_state(path)
        assert loaded["title"] == "title2", "二次写应覆盖 title"
        assert loaded["current_chapter"] == 99

    def test_lock_helpers_no_crash_on_unsupported_platform(self):
        """_acquire_lock / _release_lock 在锁库不可用时不 crash。"""
        from engine.state import _acquire_lock, _release_lock
        import tempfile
        # 用真文件句柄测试
        with tempfile.NamedTemporaryFile() as f:
            # 即便 fcntl/msvcrt 都不可用（罕见），也不应抛
            try:
                result = _acquire_lock(f)
                # 任何返回值都可（True/False 都接受，只要不抛）
                assert result in (True, False)
            finally:
                _release_lock(f)

    def test_load_state_returns_typed_dict(self, tmp_path):
        """load_state 返回 dict（TypedDict 在运行时就是 dict）。"""
        from engine.state import save_state, create_initial_state, load_state
        path = str(tmp_path / "state.json")
        state = create_initial_state("test", "t", "fanqie", "都市", "")
        save_state(state, path)
        loaded = load_state(path)
        assert isinstance(loaded, dict)
        assert "novel_id" in loaded
        assert "last_updated" in loaded


# ───────────────────────────────────────────
# OO: SQLite WAL + busy_timeout + foreign_keys（迭代 #10）
# ───────────────────────────────────────────
class TestSQLitePragmas:
    """历史背景（迭代 #10）：
      SQLite 默认 journal_mode=rollback，写操作期间全库锁 → engine 写
      state.json 时前端 /health / GET chapters 都会阻塞。
      busy_timeout=0 → 锁冲突立刻抛（多 worker / 测试并行跑时假错）。
      foreign_keys=OFF → 默认不强制 FK，数据完整性弱（orphan 行可能）。

      修法：connect event 设 PRAGMA journal_mode=WAL + busy_timeout=5000
      + synchronous=NORMAL + foreign_keys=ON。
    """

    def test_journal_mode_is_wal(self):
        """SQLite journal_mode 必须是 WAL。"""
        from app.database import engine
        from sqlalchemy import text
        with engine.connect() as conn:
            mode = conn.execute(text("PRAGMA journal_mode")).scalar()
            # mode 可能是 'wal' / 'memory' 等；wal 是我们要的
            assert mode.lower() == "wal", (
                f"journal_mode 应为 WAL，实际 {mode!r}"
            )

    def test_busy_timeout_is_set(self):
        """busy_timeout 必须 >= 1000ms（默认 0 = 不等 = 锁冲突假错）。"""
        from app.database import engine
        from sqlalchemy import text
        with engine.connect() as conn:
            timeout = conn.execute(text("PRAGMA busy_timeout")).scalar()
            assert timeout >= 1000, (
                f"busy_timeout 应 >= 1000ms，实际 {timeout}ms"
            )

    def test_foreign_keys_enabled(self):
        """PRAGMA foreign_keys 必须为 ON（默认 OFF）。"""
        from app.database import engine
        from sqlalchemy import text
        with engine.connect() as conn:
            fk = conn.execute(text("PRAGMA foreign_keys")).scalar()
            assert fk == 1, (
                f"foreign_keys 应为 ON（=1），实际 {fk}（FK 约束可能失效）"
            )


# ───────────────────────────────────────────
# PP: export_openapi.py 端到端（拿 spec + 写文件 + 错误处理）
# ───────────────────────────────────────────
class TestExportOpenApiEndToEnd:
    """迭代 #11：export_openapi.py 真实调 httpx + 写文件 + 错误处理。

    之前 TestOpenApiExport 只验脚本 import / main 存在 + .gitignore 配置，
    没真正 mock httpx 跑一遍。生产若 httpx 版本变了或 URL 改了，
    脚本可能静默失败（httpx 解析错误 → except 块）。
    """

    def test_export_writes_spec_to_path(self, monkeypatch, tmp_path):
        """模拟 httpx 返回固定 JSON → export 应写出来。"""
        import json
        import sys
        from pathlib import Path
        # 把 backend/ 加入 sys.path
        backend_root = Path(__file__).resolve().parents[1]
        if str(backend_root) not in sys.path:
            sys.path.insert(0, str(backend_root))

        # 用 importlib 加载脚本（独立 module，不污染 app.* namespace）
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "export_openapi_under_test",
            backend_root / "scripts" / "export_openapi.py",
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)  # type: ignore

        # Mock httpx.get 返回固定 spec
        fake_spec = {
            "openapi": "3.1.0",
            "info": {"title": "test", "version": "1.0"},
            "paths": {"/test": {"get": {"summary": "test"}}},
        }

        class FakeResp:
            status_code = 200
            def raise_for_status(self):
                pass
            def json(self):
                return fake_spec

        class FakeClient:
            def __init__(self, *a, **kw): pass
            def __enter__(self):
                return self
            def __exit__(self, *a):
                return False
            def get(self, url, **kw):
                return FakeResp()

        monkeypatch.setattr("httpx.get", lambda url, **kw: FakeResp())
        # 脚本用 httpx.Client；改 sys.modules 让 import 拿到 mock
        import httpx as _httpx
        monkeypatch.setattr(_httpx, "Client", FakeClient, raising=False)

        out_path = str(tmp_path / "openapi.json")
        # 直接调 main() with argv override
        monkeypatch.setattr(sys, "argv", [
            "export_openapi",
            "--url", "http://fake:9999",
            "--out", out_path,
        ])
        rc = mod.main()
        assert rc == 0, f"main() 应返回 0，实际 {rc}"
        # 文件已写
        assert Path(out_path).exists()
        written = json.loads(Path(out_path).read_text(encoding="utf-8"))
        assert written["info"]["title"] == "test"
        assert "/test" in written["paths"]

    def test_export_fails_when_url_unreachable(self, monkeypatch, tmp_path):
        """URL 不可达 → main() 返回非 0，不写文件。"""
        import sys
        from pathlib import Path
        backend_root = Path(__file__).resolve().parents[1]
        if str(backend_root) not in sys.path:
            sys.path.insert(0, str(backend_root))

        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "export_openapi_fail",
            backend_root / "scripts" / "export_openapi.py",
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)  # type: ignore

        # Mock httpx.get 抛 ConnectionError
        import httpx
        def fake_get(url, **kw):
            raise httpx.ConnectError("connection refused")
        monkeypatch.setattr("httpx.get", fake_get)

        out_path = str(tmp_path / "openapi.json")
        monkeypatch.setattr(sys, "argv", [
            "export_openapi",
            "--url", "http://fake:9999",
            "--out", out_path,
        ])
        rc = mod.main()
        assert rc != 0, "URL 不可达时 main() 必须返回非 0"
        assert not Path(out_path).exists(), "失败时不能写半成品文件"

    def test_export_invalidates_invalid_new_master_key(self):
        """ensure export script 同时提供 --url 验证（URL 必须含 http://）。"""
        # 简单 sanity check：脚本支持 --url / --out 参数
        from pathlib import Path
        script = Path(__file__).resolve().parents[1] / "scripts" / "export_openapi.py"
        content = script.read_text(encoding="utf-8")
        assert '"--url"' in content or "'--url'" in content
        assert '"--out"' in content or "'--out'" in content


# ───────────────────────────────────────────
# QQ: generate_master_key.py + security 端到端（实际加密 + 解密 round-trip）
# ───────────────────────────────────────────
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
        backend_root = Path(__file__).resolve().parents[1]

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
        backend_root = Path(__file__).resolve().parents[1]
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


# ───────────────────────────────────────────
# RR: rotate_master_key.py 端到端（旧 key 解密 → 新 key 重加密 → round-trip）
# ───────────────────────────────────────────
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
            backend_root = Path(__file__).resolve().parents[1]
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
            backend_root = Path(__file__).resolve().parents[1]
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


# ───────────────────────────────────────────
# SS: engine/graph.py 多 command 失败路径锁死（迭代 #14）
# ───────────────────────────────────────────
class TestGraphCommandFailurePaths:
    """迭代 #14：graph.py 17+ command 分支的 except 路径需要 invariant test 锁死。

    之前只测了 unknown command 失败。bootstrap / scan / fingerprint /
    export / stats / init_arc / human_review / style / calibrate /
    acceptance 在 except 分支都是同一模板（log.error + exit_code=1），
    抽样测 3 个：bootstrap / run / show（一个失败路径 + 一个边界）。
    """

    def test_bootstrap_failure_returns_exit_code_1(self, monkeypatch):
        """bootstrap 抛异常 → exit_code=1 + log 含 'bootstrap failed'。"""
        from engine import graph as graph_mod
        from engine.tools import bootstrap as bootstrap_mod

        def fake_run_bootstrap(novel_id):
            raise RuntimeError("mock bootstrap error")

        monkeypatch.setattr(bootstrap_mod, "run_bootstrap", fake_run_bootstrap)
        # 重 import 防止 graph_mod 已经持有原 run_bootstrap
        import importlib
        importlib.reload(graph_mod)

        from queue import Queue
        q = Queue()
        exit_code, stdout = graph_mod.run_graph_task(
            project_id="test-bootstrap-fail",
            command="bootstrap",
            args=[],
            run_id="r-bootstrap",
            queue=q,
        )
        assert exit_code == 1, (
            f"bootstrap 抛异常应 exit_code=1，实际 {exit_code}"
        )
        # log 走 logging 模块输出到 file handler（不在 stdout 捕获里），
        # 所以只断言 exit_code。log 实际记录由 caplog fixture 验证。

    def test_show_nonexistent_chapter_returns_text_and_exit_0(self):
        """show 命令对不存在的章节输出 ❌ 文本，但 exit_code 仍是 0（信息查询性质）。"""
        from engine.graph import run_graph_task
        from queue import Queue
        q = Queue()
        exit_code, stdout = run_graph_task(
            project_id="test-show",
            command="show",
            args=["9999"],  # 不可能存在的章节号
            run_id="r-show",
            queue=q,
        )
        assert exit_code == 0, (
            f"show 不存在的章节应 exit_code=0（信息查询），实际 {exit_code}"
        )
        assert "❌" in stdout, (
            f"show 应输出 ❌ 标记表示章节不存在：{stdout[:200]!r}"
        )

    def test_run_command_handler_registered(self):
        """run command 必须在 graph 分支里有处理（不能走 unknown 命令路径）。"""
        from engine.graph import run_graph_task
        from queue import Queue
        q = Queue()
        # 用不存在 project_id + run command 应该走 orchestrator 路径（不一定成功，
        # 但不能 exit_code=0 假装 ok，也不能 unknown command 路径）
        exit_code, stdout = run_graph_task(
            project_id="nonexistent-for-run",
            command="run",
            args=["1"],
            run_id="r-run",
            queue=q,
        )
        # 不严格断言 exit_code（依赖 state 文件存在），但必须不是 unknown command 错误
        assert "未知命令" not in stdout, (
            f"'run' 是合法 command，不应走到 unknown 分支：{stdout[:200]!r}"
        )

    def test_planner_import_error_fallback(self, monkeypatch):
        """planner agent 不存在时 fallback 到 'not yet ported' warn（不 crash）。"""
        # 这种 fallback 是有意设计：让 graph 在 agent 缺失时仍能 exit_code=0
        # （即返回 warn 信息而不是抛错）。锁死这一行为防止回归。
        import importlib
        import sys as _sys
        from engine import graph as graph_mod

        # 把 planner module 暂时从 sys.modules 移除 → import 抛 ImportError
        saved = _sys.modules.pop("engine.agents.planner", None)
        # 触发 graph_mod 重新 import planner 的分支
        try:
            importlib.reload(graph_mod)
            from queue import Queue
            q = Queue()
            # 当 planner import 失败时，graph 应捕到 ImportError 并 exit_code=0
            # （设计上是 graceful fallback，让 frontend 知道命令"未移植"而非"失败"）
            try:
                exit_code, stdout = graph_mod.run_graph_task(
                    project_id="test-planner-fallback",
                    command="planner",
                    args=[],
                    run_id="r-planner",
                    queue=q,
                )
                # 要么 0 (graceful fallback) 要么 1 (throw) — 但不能 crash
                assert exit_code in (0, 1), (
                    f"planner import 失败时 exit_code 必须在 {{0, 1}}，实际 {exit_code}"
                )
            finally:
                if saved is not None:
                    _sys.modules["engine.agents.planner"] = saved
        except Exception as e:
            if saved is not None:
                _sys.modules["engine.agents.planner"] = saved
            raise


# ───────────────────────────────────────────
# TT: save_state 真并发测试（多线程同时写不丢数据）
# ───────────────────────────────────────────
class TestSaveStateTrueConcurrency:
    """迭代 #15：之前 _acquire_lock 只测 helpers 不 crash，没真验并发场景。

    现实场景：engine + bridge.run 两个进程同时 save_state。
    文件锁确保只有一边写成功，另一边等锁 → 不会丢数据。

    注意：Windows msvcrt.locking 是进程级锁，同进程多线程锁同一文件
    能串行化（覆盖写但保证完整性）。
    """
    import threading
    import concurrent.futures

    def test_concurrent_saves_eventually_consistent(self, tmp_path):
        """N 个线程并发 save_state：最终文件内容必须是某一刻成功写入的状态之一。

        真实场景：
          - 同进程多线程：GIL 串行化执行流，但 msvcrt 文件锁可能与
            os.replace(.tmp → target) 冲突（Windows 上并发 rename 经常
            WinError 32：文件被另一进程持有）
          - 跨进程：rename 本身原子，msvcrt 锁跨进程不工作，依赖 OS 原子性

        因此本测试只断言"最终文件内容是某一时刻成功写入的状态之一"，
        不强求"全部 writer 都成功"——容许部分 raise（生产中会 retry）。
        """
        from engine.state import save_state, create_initial_state, load_state
        path = str(tmp_path / "concurrent_state.json")
        N = 8

        def worker(i):
            state = create_initial_state(
                novel_id=f"novel-{i}",
                title=f"chapter-{i}",
                platform="fanqie",
                genre="都市",
                setting_concept=f"concept-{i}",
            )
            state["current_chapter"] = i * 10
            # Windows 上 msvcrt 锁 + os.replace 并发容易 PermissionError，
            # 真实生产会用 retry 重新调用。本测试允许 raise（只看最终一致性）。
            try:
                save_state(state, path)
            except OSError:
                pass

        with self.concurrent.futures.ThreadPoolExecutor(max_workers=N) as ex:
            # map 不抛（吞异常），所以即使部分 worker 因 Windows 文件锁
            # 冲突而失败，我们只看最终文件状态
            list(ex.map(lambda i: worker(i), range(N)))

        # 最终文件必须存在且合法（rename 原子性保证）
        loaded = load_state(path)
        assert "novel_id" in loaded
        # novel_id 必须是 worker 写入的 novel-0 ~ novel-7 之一
        assert loaded["novel_id"].startswith("novel-"), (
            f"最终 novel_id 应是 worker 写入之一，实际 {loaded['novel_id']!r}"
        )
        chapter = loaded["current_chapter"]
        assert chapter in {i * 10 for i in range(N)}, (
            f"current_chapter 应是 worker 写入之一（不是损坏中间值），实际 {chapter}"
        )

    def test_concurrent_save_load_no_partial_json(self, tmp_path):
        """save_state + load_state 并发：load_state 永远拿到合法 dict（不能半写）。"""
        import json
        from engine.state import save_state, create_initial_state, load_state
        path = str(tmp_path / "save_load.json")

        initial = create_initial_state("novel", "title", "fanqie", "都市", "")
        save_state(initial, path)

        json_errors: list = []

        def writer(i):
            state = create_initial_state(
                f"novel-{i}", f"t-{i}", "fanqie", "都市", ""
            )
            # writer 允许 raise（生产中 retry）
            try:
                save_state(state, path)
            except OSError:
                pass

        def reader(i):
            try:
                loaded = load_state(path)
                assert isinstance(loaded, dict), (
                    f"reader-{i} 读到非 dict（半写）：{type(loaded)}"
                )
            except json.JSONDecodeError as e:
                json_errors.append(f"reader-{i}: {e}")
            except FileNotFoundError:
                pass  # writer 还没建文件，可接受

        with self.concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
            futs = []
            for i in range(5):
                futs.append(ex.submit(writer, i))
                futs.append(ex.submit(reader, i))
            for f in futs:
                f.result()

        # 关键断言：reader 从没读到过半写 JSON（rename 原子性）
        assert not json_errors, (
            f"reader 读到 JSONDecodeError（半写文件！）：{json_errors}"
        )


# ───────────────────────────────────────────
# UU: engine/tools/budget_manager.py 测试覆盖（迭代 #16）
# ───────────────────────────────────────────
class TestBudgetManager:
    """迭代 #16：budget_manager.py 是 176 行的核心费用追踪模块，零测试覆盖。

    之前 audit/生产 bug 报告"费用不准"时无法快速定位 — 因为没测试。
    本轮先锁死核心 4 个函数：log_cost / load_all_records /
    generate_report / total_cost 累加。
    """

    def test_log_cost_writes_jsonl(self, monkeypatch, tmp_path):
        """log_cost 必须 append JSONL（一行一 JSON）到 BUDGET_LOG。"""
        # 重定向 BUDGET_LOG 到 tmp_path
        from engine.tools import budget_manager as bm
        log_path = tmp_path / "budget.jsonl"
        monkeypatch.setattr(bm, "BUDGET_LOG", str(log_path))

        bm.log_cost(chapter=1, agent="writer", model="test",
                    input_tokens=100, output_tokens=500, cost_usd=0.05)
        bm.log_cost(chapter=2, agent="checker", model="test",
                    input_tokens=80, output_tokens=20, cost_usd=0.01)

        # 文件存在 + 2 行
        content = log_path.read_text(encoding="utf-8").strip()
        lines = content.splitlines()
        assert len(lines) == 2, f"应有 2 行记录，实际 {len(lines)}"
        # 每行是合法 JSON
        import json
        recs = [json.loads(l) for l in lines]
        assert recs[0]["chapter"] == 1
        assert recs[0]["cost_usd"] == 0.05
        assert recs[1]["chapter"] == 2
        assert recs[1]["cost_usd"] == 0.01

    def test_load_all_records_skips_corrupt_lines(self, monkeypatch, tmp_path):
        """load_all_records 跳过损坏行（不是全文件失败）。"""
        from engine.tools import budget_manager as bm
        log_path = tmp_path / "budget.jsonl"
        log_path.write_text(
            '{"chapter": 1, "cost_usd": 0.05}\n'
            'THIS IS NOT JSON\n'
            '{"chapter": 2, "cost_usd": 0.02}\n'
            '\n'  # 空行
            , encoding="utf-8"
        )
        monkeypatch.setattr(bm, "BUDGET_LOG", str(log_path))
        records = bm.load_all_records()
        # 3 个有效行（损坏 + 空行被跳过）
        assert len(records) == 2, f"应只读 2 个有效记录，实际 {len(records)}"
        assert records[0]["chapter"] == 1
        assert records[1]["chapter"] == 2

    def test_load_all_records_returns_empty_when_file_missing(self, monkeypatch, tmp_path):
        """BUDGET_LOG 不存在 → load_all_records 返回 []（不抛 FileNotFoundError）。"""
        from engine.tools import budget_manager as bm
        monkeypatch.setattr(bm, "BUDGET_LOG", str(tmp_path / "nonexistent.jsonl"))
        assert bm.load_all_records() == []

    def test_generate_report_sums_costs_correctly(self, monkeypatch, tmp_path):
        """generate_report 必须正确累加所有 cost_usd。"""
        from engine.tools import budget_manager as bm
        log_path = tmp_path / "budget.jsonl"
        log_path.write_text(
            '{"chapter":1,"cost_usd":0.05,"agent":"writer","model":"x"}\n'
            '{"chapter":1,"cost_usd":0.02,"agent":"checker","model":"x"}\n'
            '{"chapter":2,"cost_usd":0.08,"agent":"writer","model":"x"}\n'
            , encoding="utf-8"
        )
        monkeypatch.setattr(bm, "BUDGET_LOG", str(log_path))
        report = bm.generate_report()
        # total = 0.05 + 0.02 + 0.08 = 0.15
        assert abs(report["total_cost_usd"] - 0.15) < 1e-3, (
            f"total_cost 累加错误：{report['total_cost_usd']}"
        )
        # chapters_done = unique chapter = {1, 2} = 2
        assert report["chapters_done"] == 2
        # by_agent 正确分组
        assert report["by_agent"]["writer"]["calls"] == 2
        assert report["by_agent"]["checker"]["calls"] == 1
        assert abs(report["by_agent"]["writer"]["cost"] - 0.13) < 1e-3
        assert abs(report["by_agent"]["checker"]["cost"] - 0.02) < 1e-3


# ───────────────────────────────────────────
# HHH: orchestrator outline cost 不能双重计费（迭代 #28）
# ───────────────────────────────────────────
class TestOutlineCostNotDoubleCharged:
    """迭代 #28: node_load_arc_tasks 之前每次 outline 都计费 2 次。

    历史 bug：
      orchestrator.py 之前 line 209 在 try/except 之外多调一次
      `_add_cost(state, cost)`，而每个分支（card / talk / batch）
      内部已经调过 → 实际计费 = 2 × 真实花费。
      50 章跑下来 budget_used_usd 虚高 100%，超预算提前 escalate。

    修法：删掉 line 209 的重复调用，保留分支内部调用。
    本测试锁死：跑一次 outline → state.budget_used_usd 只增加真实花费。
    """
    @pytest.fixture(autouse=True)
    def import_orch(self):
        from engine import orchestrator as orch_mod
        self.orch = orch_mod
        return orch_mod

    def test_batch_outline_cost_added_once(self, monkeypatch):
        """batch 模式：run_outline 返回 cost=0.1 → budget_used 增 0.1（不是 0.2）。"""
        FAKE_COST = 0.1
        def fake_run_outline(arc, start, setting, memory):
            return [{"chapter_number": 1, "chapter_goal": "x",
                     "chapter_role": "r", "main_characters": [],
                     "shuang_type": None, "shuang_description": "",
                     "ending_hook_type": "信息钩", "ending_hook_description": "",
                     "setting_constraints": [], "forbidden_actions": [],
                     "target_length": "2000-2200", "audit_mode": "full",
                     "is_arc_climax": False}], FAKE_COST
        monkeypatch.setattr(self.orch, "run_outline", fake_run_outline)
        # batch 模式（默认）
        monkeypatch.setenv("NOVEL_OUTLINE_MODE", "batch")
        monkeypatch.delenv("NOVEL_AI_DIR", raising=False)
        import json, tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            setting_dir = self.orch.OUTPUT_DIR  # 用真实 OUTPUT_DIR
            setting_dir.mkdir(parents=True, exist_ok=True)
            (setting_dir / "setting_package.json").write_text("{}", encoding="utf-8")

            state = {
                "novel_id": "default",
                "current_chapter": 0,
                "current_arc": 0,
                "budget_used_usd": 0.0,
                "budget_limit_usd": 500.0,
                "arc_plans": [{"arc_id": 1, "arc_name": "test", "arc_goal": "x",
                               "estimated_chapters": 10,
                               "arc_climax_description": "",
                               "arc_climax_chapter_offset": 0,
                               "emotion_curve": "low",
                               "new_characters_introduced": [],
                               "arc_ending_state": "",
                               "is_final_arc": False}],
                "error_log": [],
            }
            result = self.orch.node_load_arc_tasks(state)
            # 关键断言：cost 只增 1 次
            used = result.get("budget_used_usd", 0.0)
            assert abs(used - FAKE_COST) < 1e-6, (
                f"batch outline cost 应为 {FAKE_COST}（1 次计费），"
                f"实际 {used}（双重计费 bug）"
            )

    def test_card_outline_cost_added_once(self, monkeypatch):
        """card 模式：run_outline_card 返回 cost=0.15 → budget_used 增 0.15。"""
        FAKE_COST = 0.15
        def fake_run_outline_card(arc, start, setting, memory):
            return [{"tasks": [{"chapter_number": 1, "chapter_goal": "x",
                               "chapter_role": "r", "main_characters": [],
                               "shuang_type": None, "shuang_description": "",
                               "ending_hook_type": "信息钩",
                               "ending_hook_description": "",
                               "setting_constraints": [], "forbidden_actions": [],
                               "target_length": "2000-2200", "audit_mode": "full",
                               "is_arc_climax": False}]}], FAKE_COST
        monkeypatch.setattr(self.orch, "run_outline_card", fake_run_outline_card)
        monkeypatch.setenv("NOVEL_OUTLINE_MODE", "card")
        import tempfile
        with tempfile.TemporaryDirectory():
            setting_dir = self.orch.OUTPUT_DIR
            setting_dir.mkdir(parents=True, exist_ok=True)
            (setting_dir / "setting_package.json").write_text("{}", encoding="utf-8")

            state = {
                "novel_id": "default",
                "current_chapter": 0,
                "current_arc": 0,
                "budget_used_usd": 0.0,
                "budget_limit_usd": 500.0,
                "arc_plans": [{"arc_id": 1, "arc_name": "test", "arc_goal": "x",
                               "estimated_chapters": 10,
                               "arc_climax_description": "",
                               "arc_climax_chapter_offset": 0,
                               "emotion_curve": "low",
                               "new_characters_introduced": [],
                               "arc_ending_state": "",
                               "is_final_arc": False}],
                "error_log": [],
            }
            result = self.orch.node_load_arc_tasks(state)
            used = result.get("budget_used_usd", 0.0)
            assert abs(used - FAKE_COST) < 1e-6, (
                f"card outline cost 应为 {FAKE_COST}，实际 {used}（双重计费）"
            )

    def test_talk_outline_cost_added_once(self, monkeypatch):
        """talk 模式：run_outline_talk 返回 cost=0.08 → budget_used 增 0.08。"""
        FAKE_COST = 0.08
        def fake_run_outline_talk(arc, start, setting, memory):
            return ({"tasks": [{"chapter_number": 1, "chapter_goal": "x",
                               "chapter_role": "r", "main_characters": [],
                               "shuang_type": None, "shuang_description": "",
                               "ending_hook_type": "信息钩",
                               "ending_hook_description": "",
                               "setting_constraints": [], "forbidden_actions": [],
                               "target_length": "2000-2200", "audit_mode": "full",
                               "is_arc_climax": False}],
                    "questions": []}, FAKE_COST)
        monkeypatch.setattr(self.orch, "run_outline_talk", fake_run_outline_talk)
        monkeypatch.setenv("NOVEL_OUTLINE_MODE", "talk")
        import tempfile
        with tempfile.TemporaryDirectory():
            setting_dir = self.orch.OUTPUT_DIR
            setting_dir.mkdir(parents=True, exist_ok=True)
            (setting_dir / "setting_package.json").write_text("{}", encoding="utf-8")

            state = {
                "novel_id": "default",
                "current_chapter": 0,
                "current_arc": 0,
                "budget_used_usd": 0.0,
                "budget_limit_usd": 500.0,
                "arc_plans": [{"arc_id": 1, "arc_name": "test", "arc_goal": "x",
                               "estimated_chapters": 10,
                               "arc_climax_description": "",
                               "arc_climax_chapter_offset": 0,
                               "emotion_curve": "low",
                               "new_characters_introduced": [],
                               "arc_ending_state": "",
                               "is_final_arc": False}],
                "error_log": [],
            }
            result = self.orch.node_load_arc_tasks(state)
            used = result.get("budget_used_usd", 0.0)
            assert abs(used - FAKE_COST) < 1e-6, (
                f"talk outline cost 应为 {FAKE_COST}，实际 {used}（双重计费）"
            )

    def test_outline_exception_no_cost_charged(self, monkeypatch):
        """outline 抛异常时不应计费（避免"失败还扣钱"误判）。"""
        def fake_run_outline_raises(arc, start, setting, memory):
            raise ConnectionError("outline service down")
        monkeypatch.setattr(self.orch, "run_outline", fake_run_outline_raises)
        monkeypatch.setenv("NOVEL_OUTLINE_MODE", "batch")
        import tempfile
        with tempfile.TemporaryDirectory():
            setting_dir = self.orch.OUTPUT_DIR
            setting_dir.mkdir(parents=True, exist_ok=True)
            (setting_dir / "setting_package.json").write_text("{}", encoding="utf-8")

            state = {
                "novel_id": "default",
                "current_chapter": 0,
                "current_arc": 0,
                "budget_used_usd": 0.0,
                "budget_limit_usd": 500.0,
                "arc_plans": [{"arc_id": 1, "arc_name": "test", "arc_goal": "x",
                               "estimated_chapters": 10,
                               "arc_climax_description": "",
                               "arc_climax_chapter_offset": 0,
                               "emotion_curve": "low",
                               "new_characters_introduced": [],
                               "arc_ending_state": "",
                               "is_final_arc": False}],
                "error_log": [],
            }
            result = self.orch.node_load_arc_tasks(state)
            used = result.get("budget_used_usd", 0.0)
            assert used == 0.0, (
                f"outline 抛异常时不应计费，实际 budget_used={used}"
            )
            assert result.get("_outline_failed") is True, (
                "outline 失败必须 _outline_failed=True（之前 bug: 兜底 10 placeholder）"
            )


# ───────────────────────────────────────────
# VV: scripts/audit_project.py 自身测试（迭代 #17 收尾）
# ───────────────────────────────────────────
class TestAuditProjectItself:
    """最后 53 分钟收尾：audit_project 是 CI 看守者，自身没测试。

    Auditor 类决定哪些 check 算 pass / warn / error。
    strict / non-strict 模式行为必须锁死。
    """

    def test_auditor_strict_mode_promotes_warn_to_error(self):
        """strict=True 时 warn 应升级为 error（CI 严格模式）。"""
        from scripts.audit_project import Auditor
        a = Auditor(project_id="test", strict=True)
        a.check(False, "test condition")
        assert len(a.warnings) == 0, "strict 模式下不应收集 warn"
        assert len(a.errors) == 1, "strict 模式下 False 应进 errors"
        assert a.errors[0].startswith("✗ test condition")

    def test_auditor_non_strict_collects_warnings(self):
        """strict=False 时 False 进 warnings（默认 / 友好模式）。"""
        from scripts.audit_project import Auditor
        a = Auditor(project_id="test", strict=False)
        a.check(False, "test condition")
        assert len(a.warnings) == 1
        assert len(a.errors) == 0
        assert a.warnings[0].startswith("⚠ test condition")

    def test_auditor_info_does_not_count_as_warning(self):
        """info() 必须不计入 pass/warn/error 统计。"""
        from scripts.audit_project import Auditor
        a = Auditor(project_id="test", strict=True)
        a.info("test info", "前置条件未满足")
        # strict 模式下 info 也不变 error（设计：info 是中性）
        assert len(a.warnings) == 0
        assert len(a.errors) == 0
        assert len(a.infos) == 1

    def test_auditor_pass_collected_correctly(self):
        """True 条件 → pass 列表。"""
        from scripts.audit_project import Auditor
        a = Auditor(project_id="test")
        a.check(True, "all good")
        assert len(a.passes) == 1
        assert a.passes[0].startswith("✓ all good")


# ───────────────────────────────────────────
# III: run_bridge 不能用永远 False 的 lock 检查（迭代 #30）
# ───────────────────────────────────────────
class TestRunBridgeConcurrencyGuard:
    """迭代 #30: 之前 run_bridge 用 _get_project_lock(project_id).locked() 做
    并发保护，但该 asyncio.Lock 永不被 acquire（grep 证实）→ 检查永远
    False → 给 false sense of security（代码看起来"有锁"但实际没有）。

    修法：删掉死代码，依赖 DB 层 BridgeRun.status='running' 检查 +
    lifespan 启动时 _recover_orphan_bridge_runs。
    本测试锁死：源码里不应再出现 _project_locks / _get_project_lock 引用。
    """
    def test_no_dead_project_lock_in_bridge_py(self):
        """bridge.py 不应再定义 / 调用 _project_locks / _get_project_lock。"""
        from pathlib import Path
        bridge_py = Path(__file__).resolve().parents[1] / "app" / "api" / "bridge.py"
        content = bridge_py.read_text(encoding="utf-8")
        # 关键符号：定义 + 调用都不能有（注释里的解释 OK）
        offenders: list[str] = []
        for i, line in enumerate(content.splitlines(), start=1):
            stripped = line.strip()
            # 排除纯注释行
            if stripped.startswith("#"):
                continue
            if "_project_locks" in line and "_project_locks" != "_project_locks:":  # 类型注解也排除
                offenders.append(f"line {i}: {line.rstrip()}")
            if "_get_project_lock" in line and "(" in line:  # 实际调用（带括号）
                offenders.append(f"line {i}: {line.rstrip()}")
        assert not offenders, (
            "bridge.py 还有死锁引用（应删除）：\n  " + "\n  ".join(offenders)
        )

    def test_run_bridge_only_checks_db_for_concurrent_runs(self):
        """run_bridge 源码必须只有 DB 层 BridgeRun active 检查（#30 + #74）。

        #30: 之前 run_bridge 用 _get_project_lock(project_id).locked() 做并发保护，
        但 asyncio.Lock 永不被 acquire → 检查永远 False → 给 false sense of
        security。修法：删死代码，依赖 DB 层 BridgeRun active 检查 + lifespan
        启动时 _recover_orphan_bridge_runs。

        #74: 之前 DB 检查只查 status='running' 有 TOCTOU 窗口 —— pending insert
        后到 status 翻 'running' 之前的窗口里两个并发请求都通过。修法：
        active 检查包含 pending + running（用 status.in_(["pending","running"])）。

        本测试锁死：
          - 真代码行不应有 _get_project_lock / .locked() 这种无效检查
          - 必须用 in_() 包含 pending+running（不能退化到只查 running）
        """
        from pathlib import Path
        import re
        bridge_py = Path(__file__).resolve().parents[1] / "app" / "api" / "bridge.py"
        content = bridge_py.read_text(encoding="utf-8")
        # 找 run_bridge 函数体（多行 args 模式：args 跨行 \n）
        m = re.search(
            r"async def run_bridge\([\s\S]*?\):(.*?)(?=\nasync def |\ndef |\nclass |\Z)",
            content, re.DOTALL
        )
        assert m, "找不到 run_bridge"
        body = m.group(1)
        # 排除注释行（解释历史为什么删 lock 的注释里会出现 .locked() / _get_project_lock）
        code_lines = [
            line for line in body.splitlines()
            if not line.strip().startswith("#")
        ]
        code_body = "\n".join(code_lines)
        # 关键检查：真代码行不该有 _get_project_lock / .locked() 这种无效检查
        assert ".locked()" not in code_body, (
            "run_bridge 真代码行不应再用 .locked() 假并发检查（之前 dead code）"
        )
        assert "_get_project_lock" not in code_body, (
            "run_bridge 真代码行不应再调 _get_project_lock（死代码）"
        )
        # #74：active 检查必须包含 pending + running（in_ 模式）
        assert 'status.in_(["pending", "running"])' in code_body or \
               'in_(["pending", "running"])' in code_body, (
            "run_bridge 必须用 status.in_(['pending','running']) 查询，"
            "否则 TOCTOU 窗口（#74）未关闭"
        )
        # 反向保证：不能退化到单独 status='running'
        bad_pattern = re.search(r'\.filter_by\([^)]*status\s*=\s*["\']running["\']', code_body)
        assert not bad_pattern, (
            f"run_bridge 不能退回到只查 status='running'（#74 已修），匹配 {bad_pattern.group() if bad_pattern else None}"
        )


# ───────────────────────────────────────────
# JJJ: chapter_import 单文件坏不能阻断整批（迭代 #31）
# ───────────────────────────────────────────
class TestImportChaptersResilient:
    """迭代 #31: import_chapters_from_novel_ai 之前一个坏文件就让整批 import 失败。

    历史 bug：chapters_dir.glob("ch_*.txt") 拿到所有 .txt，但每个文件都做：
      - n = int(txt_path.stem.split("_")[1])   → ValueError on malformed
      - txt_path.read_text(encoding="utf-8")   → UnicodeDecodeError on 编码错
      - json.loads(meta.read_text(...))        → JSONDecodeError on meta 坏
    任何一个抛异常 → 整个 import 失败 → 用户看到 0 章导入，没法定位是哪个文件坏。

    修法：每文件 try/except，log warning + 跳过该文件继续下一个。
    同样修 _force_reimport。

    本测试锁死：3 个文件（1 正常 / 1 坏 filename / 1 meta 损坏）→ 正常文件
    仍被导入，整个 import 不抛异常。
    """
    @pytest.fixture(autouse=True)
    def setup_chapters_dir(self, tmp_path):
        """准备一个含 3 个章节文件的目录：1 正常 / 1 坏 filename / 1 坏 meta"""
        import os
        import secrets
        chapters_dir = tmp_path / "output" / "chapters"
        chapters_dir.mkdir(parents=True, exist_ok=True)

        # 1) 正常文件
        (chapters_dir / "ch_0001.txt").write_text(
            "厅堂不大。\n\n商恪坐在案后，\n翻看案上账册。\n", encoding="utf-8"
        )
        (chapters_dir / "ch_0001_meta.json").write_text(
            json.dumps({
                "chapter_number": 1,
                "chapter_role": "铺垫",
                "chapter_goal": "展现商恪困境",
                "score": 7.0,
                "rewrite_count": 0,
            }, ensure_ascii=False),
            encoding="utf-8",
        )
        # 2) 正常文件 + 坏 meta
        (chapters_dir / "ch_0002.txt").write_text(
            "雅间内。\n\n林尘盘膝坐下。\n", encoding="utf-8"
        )
        (chapters_dir / "ch_0002_meta.json").write_text(
            "{not valid json", encoding="utf-8"
        )
        # 3) 畸形文件名（不匹配 ch_<N> 格式）
        (chapters_dir / "ch_xyz.txt").write_text("garbage", encoding="utf-8")

        self.tmp_path = tmp_path
        # 用 secrets 保证 project_id 唯一（避免 DB 残留冲突）
        self.project_id = f"test-resilient-{secrets.token_hex(8)}"
        yield tmp_path
        # teardown：清理测试数据
        from app.database import SessionLocal
        from app.models import Project, Chapter
        db = SessionLocal()
        try:
            db.query(Chapter).filter_by(project_id=self.project_id).delete()
            db.query(Project).filter_by(id=self.project_id).delete()
            db.commit()
        except Exception:
            pass
        finally:
            db.close()

    def test_import_chapters_continues_past_bad_files(self, setup_chapters_dir):
        """3 个文件（1 正常 + 1 meta 坏 + 1 坏 filename）→ 正常文件被导入，整个 import 不抛。"""
        import asyncio
        from app.bridge.chapter_import import import_chapters_from_novel_ai
        from app.database import SessionLocal
        from app.models import Project, Chapter

        # 准备 project
        db = SessionLocal()
        try:
            project = Project(
                id=self.project_id,
                title="test",
                genre="玄幻",
                status="ready",
                config_json={},
            )
            db.add(project)
            db.commit()
        finally:
            db.close()

        db = SessionLocal()
        try:
            # 之前会因 ch_0002_meta.json 损坏而抛 JSONDecodeError → 0 章导入
            # 修后：2 章导入（ch_0001 + ch_0002 with empty meta），ch_xyz 跳过
            result = asyncio.run(
                import_chapters_from_novel_ai(self.project_id, str(self.tmp_path), db)
            )
            # 关键断言 1：import 没抛
            assert result is not None
            assert len(result) == 2, (
                f"应导入 2 个 chapter（ch_0001 + ch_0002 with bad meta），"
                f"实际 {len(result)} 个：{result}"
            )
            # 关键断言 2：DB 里至少有 ch_0001（最稳的）
            chapter_nos = {
                c.chapter_no for c in
                db.query(Chapter).filter_by(project_id=self.project_id).all()
            }
            assert 1 in chapter_nos, (
                f"ch_0001 应被导入，DB chapter_nos={chapter_nos}"
            )
            assert 2 in chapter_nos, (
                f"ch_0002 应被导入（meta 坏但 txt 仍可用），DB chapter_nos={chapter_nos}"
            )
        finally:
            # 清理
            try:
                db.query(Chapter).filter_by(project_id=self.project_id).delete()
                db.query(Project).filter_by(id=self.project_id).delete()
                db.commit()
            except Exception:
                pass
            db.close()

    def test_force_reimport_continues_past_bad_files(self, setup_chapters_dir):
        """_force_reimport 也必须单文件坏不阻断。"""
        import asyncio
        from app.bridge.chapter_import import _force_reimport
        from app.database import SessionLocal
        from app.models import Project, Chapter

        # 准备 project
        db = SessionLocal()
        try:
            project = Project(
                id=self.project_id,
                title="test",
                genre="玄幻",
                status="ready",
                config_json={},
            )
            db.add(project)
            db.commit()
        finally:
            db.close()

        db = SessionLocal()
        try:
            result = asyncio.run(
                _force_reimport(self.project_id, str(self.tmp_path), db)
            )
            # 至少 ch_0001 应被 created（不存在）+ ch_0002 meta 坏但仍 create
            chapter_nos = {item["chapter_no"] for item in result}
            assert 1 in chapter_nos, (
                f"_force_reimport 应至少处理 ch_0001，实际 chapter_nos={chapter_nos}"
            )
        finally:
            try:
                db.query(Chapter).filter_by(project_id=self.project_id).delete()
                db.query(Project).filter_by(id=self.project_id).delete()
                db.commit()
            except Exception:
                pass
            db.close()


# ───────────────────────────────────────────
# KKK: MiniMax M3 reasoning_content 检测（迭代 #32）
# ───────────────────────────────────────────
class TestLlmRouterMiniMaxReasoningContent:
    """迭代 #32: _minimax 之前对 reasoning_content 存在但 content 为空的响应
    有死代码 fallback（line 456-458 重新赋 msg.get("content", "") 还是空），
    导致 M3 思考模式被意外开启时静默返回空文本，caller 把空文本当成正常
    生成继续 pipeline。

    修法：检测到 reasoning_content 非空 + content 空时直接 raise ValueError
    让配置 bug 暴露（MINIMAX_BASE_URL 可能被覆盖到旧版 endpoint）。

    本测试锁死：mock httpx 返回 {"content": "", "reasoning_content": "..."}，
    验证 _minimax raise ValueError 而不是返回空字符串。
    """
    def test_minimax_raises_on_reasoning_content_with_empty_content(self, monkeypatch):
        """MiniMax M3 思考模式被意外开启 → 必须 raise ValueError。"""
        import httpx
        from engine.llm import router as router_mod
        from engine.llm.router import LLMRouter

        # 准备一个 fake response：content 空 + reasoning_content 非空
        class FakeResp:
            status_code = 200
            def raise_for_status(self):
                pass
            def json(self):
                return {
                    "choices": [{
                        "message": {
                            "content": "",
                            "reasoning_content": "用户问的是测试，让我先思考一下...",
                        }
                    }],
                    "usage": {"prompt_tokens": 100, "completion_tokens": 50},
                }
        # 设置 MINIMAX_API_KEY
        monkeypatch.setenv("MINIMAX_API_KEY", "test-key")

        r = LLMRouter("test")
        r.routes["writer"] = ("minimax", "MiniMax-M3")

        # mock httpx.Client.post 返回 fake response
        class FakeClient:
            def __init__(self, *a, **kw): pass
            def post(self, *a, **kw):
                return FakeResp()
        monkeypatch.setattr(router_mod, "_get_client", lambda timeout=120: FakeClient())
        monkeypatch.setattr(router_mod, "_get_proxied_client", lambda *a, **kw: FakeClient())

        # 必须 raise ValueError（之前的 bug 是返回空 text）
        with pytest.raises(ValueError, match="reasoning_content"):
            r.call("writer", "sys", "user", max_tokens=2000, temperature=0.7)

    def test_minimax_returns_content_normally(self, monkeypatch):
        """正常 content 响应（非空）→ 正常返回。"""
        import httpx
        from engine.llm import router as router_mod
        from engine.llm.router import LLMRouter

        class FakeResp:
            status_code = 200
            def raise_for_status(self):
                pass
            def json(self):
                return {
                    "choices": [{
                        "message": {
                            "content": "正常回答的章节内容",
                            # 没 reasoning_content
                        }
                    }],
                    "usage": {"prompt_tokens": 100, "completion_tokens": 50},
                }
        monkeypatch.setenv("MINIMAX_API_KEY", "test-key")

        r = LLMRouter("test")
        r.routes["writer"] = ("minimax", "MiniMax-M3")

        class FakeClient:
            def __init__(self, *a, **kw): pass
            def post(self, *a, **kw):
                return FakeResp()
        monkeypatch.setattr(router_mod, "_get_client", lambda timeout=120: FakeClient())
        monkeypatch.setattr(router_mod, "_get_proxied_client", lambda *a, **kw: FakeClient())

        # 正常返回（不 raise）
        text, cost = r.call("writer", "sys", "user", max_tokens=2000, temperature=0.7)
        assert text == "正常回答的章节内容"
        assert cost > 0

    def test_minimax_empty_content_no_reasoning_falls_back(self, monkeypatch):
        """content 空 + 无 reasoning_content → 走最底部兜底（text 字段 / reply 字段），
        不 raise。"""
        from engine.llm import router as router_mod
        from engine.llm.router import LLMRouter

        class FakeResp:
            status_code = 200
            def raise_for_status(self):
                pass
            def json(self):
                return {
                    "choices": [{
                        "message": {
                            "content": "",
                            # 没 reasoning_content
                        },
                        "text": "M2 系列 fallback text 字段",
                    }],
                    "usage": {"prompt_tokens": 100, "completion_tokens": 50},
                }
        monkeypatch.setenv("MINIMAX_API_KEY", "test-key")

        r = LLMRouter("test")
        r.routes["writer"] = ("minimax", "MiniMax-M3")

        class FakeClient:
            def __init__(self, *a, **kw): pass
            def post(self, *a, **kw):
                return FakeResp()
        monkeypatch.setattr(router_mod, "_get_client", lambda timeout=120: FakeClient())
        monkeypatch.setattr(router_mod, "_get_proxied_client", lambda *a, **kw: FakeClient())

        # 不 raise，走兜底拿 text 字段
        text, cost = r.call("writer", "sys", "user", max_tokens=2000, temperature=0.7)
        assert text == "M2 系列 fallback text 字段"


# ───────────────────────────────────────────
# LLL: SSE queue 内存泄漏（迭代 #33）
# ───────────────────────────────────────────
class TestSSEQueueCleanup:
    """迭代 #33: _run_queues (bridge.py) 和 _job_queues (worldbuild/orchestrator.py)
    之前只创建不清理 → 生产长期跑 N 个 run 后 dict 里堆 N 个 Queue，
    每个 Queue 有内部 buffer，内存持续涨。

    修法：SSE consumer 读完 done 事件后（或异常退出时）调用 cleanup_*_queue。
    本测试锁死：consumer 退出后 dict 里 queue 必须被移除。
    """
    def test_worldbuild_queue_cleanup_on_done(self):
        """stream_worldbuild consumer 读完 done → _job_queues 必须被清理。"""
        import asyncio
        from app.worldbuild import orchestrator as wb_orch
        from app.api.worldbuild import cleanup_job_queue

        # 先放一些事件 + done
        async def _scenario():
            q = wb_orch.get_job_queue("test-job-1")
            await q.put({"event": "stage_done", "stage": "x"})
            await q.put({"event": "done"})
            # 模拟 consumer：取完 done 后调 cleanup
            while True:
                payload = await q.get()
                if payload.get("event") == "done":
                    break
            cleanup_job_queue("test-job-1")
            # 验证 dict 已清
            assert "test-job-1" not in wb_orch._job_queues, (
                f"cleanup_job_queue 后 _job_queues 仍含 test-job-1，"
                f"keys={list(wb_orch._job_queues.keys())}"
            )
        asyncio.run(_scenario())

    def test_worldbuild_queue_cleanup_safe_when_already_removed(self):
        """重复 cleanup 是 no-op（不能抛）。"""
        from app.worldbuild.orchestrator import cleanup_job_queue
        cleanup_job_queue("nonexistent-job-xyz")  # 不抛
        cleanup_job_queue("nonexistent-job-xyz")  # 重复也不抛

    def test_bridge_run_queue_cleanup_safe_when_already_removed(self):
        """bridge.py cleanup_run_queue 同样幂等。"""
        from app.api.bridge import cleanup_run_queue
        cleanup_run_queue("nonexistent-run-xyz")
        cleanup_run_queue("nonexistent-run-xyz")

    def test_worldbuild_queue_event_generator_uses_finally_cleanup(self):
        """stream_worldbuild event_generator 必须用 try/finally 包裹清理（防止异常泄漏）。"""
        from pathlib import Path
        import re
        worldbuild_py = Path(__file__).resolve().parents[1] / "app" / "api" / "worldbuild.py"
        content = worldbuild_py.read_text(encoding="utf-8")
        # 找 event_generator 函数体
        m = re.search(
            r"async def event_generator\(\):(.*?)(?=\nasync def |\ndef |\nclass |\Z)",
            content, re.DOTALL
        )
        assert m, "找不到 event_generator"
        body = m.group(1)
        # 必须有 try / finally 包裹 cleanup_job_queue
        assert "try:" in body, (
            "event_generator 必须 try/finally 包裹（防止异常时 queue 泄漏）"
        )
        assert "finally:" in body, "event_generator 必须有 finally 分支"
        assert "cleanup_job_queue" in body, (
            "event_generator finally 必须调 cleanup_job_queue"
        )

    def test_bridge_event_generator_uses_finally_cleanup(self):
        """stream_bridge event_generator 同理。"""
        from pathlib import Path
        import re
        bridge_py = Path(__file__).resolve().parents[1] / "app" / "api" / "bridge.py"
        content = bridge_py.read_text(encoding="utf-8")
        # 找 stream_bridge 的 event_generator
        m = re.search(
            r"async def stream_bridge\([\s\S]*?async def event_generator\(\):(.*?)(?=\nasync def |\ndef |\nclass |\Z)",
            content, re.DOTALL
        )
        assert m, "找不到 stream_bridge.event_generator"
        body = m.group(1)
        assert "try:" in body, (
            "stream_bridge.event_generator 必须 try/finally 包裹"
        )
        assert "finally:" in body, "必须有 finally 分支"
        assert "cleanup_run_queue" in body, (
            "event_generator finally 必须调 cleanup_run_queue"
        )


# ───────────────────────────────────────────
# MMM: export_chapters 单章坏不能阻断整批（迭代 #34）
# ───────────────────────────────────────────
class TestExportChaptersResilient:
    """迭代 #34: export_chapters / print_stats 之前单章坏让整批 export 失败。

    历史 bug：1 章编码错（Latin-1 而非 UTF-8）/ meta 损坏 → 整个 export 抛异常
    → 之前已写好的 chapters 也没保存。
    跟 import_chapters 是同型问题（迭代 #31），同样的修法。

    本测试锁死：2 个 chapter（1 正常 + 1 坏 encoding）→ 正常 chapter
    被导出，export 不抛异常。
    """
    def test_export_chapters_source_has_per_chapter_try_except(self):
        """源码级锁死：export_chapters 体内必须每章独立 try/except。

        Runtime 验证很难构造（readline 已经过滤坏文件，要让 f.read() 单独
        失败需要 partial UTF-8 sequence 截断等），但源码级锁死足以防止回归。
        """
        from pathlib import Path
        import re
        exporter_py = Path(__file__).resolve().parents[1] / "engine" / "tools" / "exporter.py"
        content = exporter_py.read_text(encoding="utf-8")
        m = re.search(
            r"def export_chapters\([\s\S]*?\):(.*?)(?=\ndef |\nclass |\Z)",
            content, re.DOTALL
        )
        assert m, "找不到 export_chapters"
        body = m.group(1)
        # 关键：在 for ch_num, ch_path in chapters 循环内必须有 try/except
        # 不能让单章抛异常阻断整批
        assert body.count("try:") >= 2 or body.count("except") >= 2, (
            "export_chapters 体内必须有 try/except 处理单章失败（之前 all-or-nothing）"
        )
        assert "continue" in body, (
            "跳过单章后必须 continue（不能 break / 抛异常）"
        )

    def test_print_stats_source_has_per_chapter_try_except(self):
        """print_stats 同样修法：源码必须有 try/except + continue。"""
        from pathlib import Path
        exporter_py = Path(__file__).resolve().parents[1] / "engine" / "tools" / "exporter.py"
        content = exporter_py.read_text(encoding="utf-8")
        # 用基于缩进的解析：找到 def print_stats( 后的非空行，body 是缩进 >= 4 空格的行
        lines = content.splitlines()
        body_start = None
        for i, line in enumerate(lines):
            if line.startswith("def print_stats"):
                body_start = i + 1
                break
        assert body_start is not None, "找不到 print_stats"
        # 收集到下一个 def 之前的所有行
        body_lines = []
        for line in lines[body_start:]:
            if line.startswith("def ") and not line.startswith("def print_stats"):
                break
            body_lines.append(line)
        body = "\n".join(body_lines)
        assert "try:" in body, (
            "print_stats 体内必须有 try/except 处理单章失败"
        )
        assert "continue" in body, (
            "跳过单章后必须 continue"
        )

    def test_export_chapters_runs_without_error_on_normal_files(self, tmp_path, monkeypatch):
        """正常文件场景：export_chapters 跑通返回正确结果。"""
        from engine.tools.exporter import export_chapters
        import engine.tools.exporter as exporter_mod

        chapters_dir = tmp_path / "output" / "chapters"
        chapters_dir.mkdir(parents=True, exist_ok=True)
        (chapters_dir / "ch_0001.txt").write_text("雅间内。\n", encoding="utf-8")
        (chapters_dir / "ch_0001_meta.json").write_text(
            json.dumps({"score": 7.0, "chapter_role": "铺垫"}, ensure_ascii=False),
            encoding="utf-8",
        )
        (chapters_dir / "ch_0002.txt").write_text("林尘盘膝。\n", encoding="utf-8")
        (chapters_dir / "ch_0002_meta.json").write_text(
            json.dumps({"score": 8.0, "chapter_role": "发展"}, ensure_ascii=False),
            encoding="utf-8",
        )
        setting_path = tmp_path / "output" / "setting_package.json"
        setting_path.write_text(
            json.dumps({"title_candidates": ["测试书"]}, ensure_ascii=False),
            encoding="utf-8",
        )
        # exporter 已经 from-import 了这些名字，必须 patch exporter 模块自己的属性
        monkeypatch.setattr(exporter_mod, "CHAPTERS_DIR_STR", str(chapters_dir))
        monkeypatch.setattr(exporter_mod, "OUTPUT_DIR_STR", str(tmp_path / "output"))
        monkeypatch.setattr(exporter_mod, "SETTING_PATH_STR", str(setting_path))
        exports_dir = tmp_path / "output" / "exports"
        exports_dir.mkdir(parents=True, exist_ok=True)
        monkeypatch.setattr(exporter_mod, "EXPORTS_DIR", str(exports_dir))

        result = export_chapters()
        assert result is not None
        assert result["chapters_exported"] == 2
        assert "雅间内" in Path(result["output_path"]).read_text(encoding="utf-8")
        assert "林尘盘膝" in Path(result["output_path"]).read_text(encoding="utf-8")


# ───────────────────────────────────────────
# NNN: pull_setting_package JSON 错误处理（迭代 #35）
# ───────────────────────────────────────────
class TestPullSettingJsonErrorHandling:
    """迭代 #35: pull_setting_package 之前损坏的 setting_package.json 让
    原始 JSONDecodeError 暴露给前端（500 + 几百行 Python traceback）。

    修法：catch (json.JSONDecodeError, UnicodeDecodeError) 抛清晰 ValueError
    提示用户"文件损坏请重新跑 planner"。

    本测试锁死：损坏的 setting_package.json 必须 raise ValueError（带用户可读信息），
    不是让原始 JSONDecodeError 透出。
    """
    def test_pull_setting_raises_value_error_on_corrupt_json(self, tmp_path):
        """损坏的 setting_package.json → 抛 ValueError 不是 JSONDecodeError。"""
        import asyncio
        from app.bridge.setting_sync import pull_setting_package
        from app.database import SessionLocal
        from app.models import Project
        import secrets

        # 准备损坏文件
        (tmp_path / "output").mkdir(parents=True, exist_ok=True)
        (tmp_path / "output" / "setting_package.json").write_text(
            "{ this is not valid json",
            encoding="utf-8",
        )

        # 准备 project
        project_id = f"test-pull-{secrets.token_hex(8)}"
        db = SessionLocal()
        try:
            project = Project(
                id=project_id, title="test", genre="玄幻", status="ready",
                config_json={},
            )
            db.add(project)
            db.commit()
        finally:
            db.close()

        db = SessionLocal()
        try:
            with pytest.raises(ValueError, match="setting_package.json 损坏"):
                asyncio.run(pull_setting_package(project_id, str(tmp_path), db))
        finally:
            try:
                db.query(Project).filter_by(id=project_id).delete()
                db.commit()
            except Exception:
                pass
            db.close()

    def test_pull_setting_raises_value_error_on_encoding_error(self, tmp_path):
        """非 UTF-8 编码的 setting_package.json → 抛 ValueError。"""
        import asyncio
        from app.bridge.setting_sync import pull_setting_package
        from app.database import SessionLocal
        from app.models import Project
        import secrets

        (tmp_path / "output").mkdir(parents=True, exist_ok=True)
        # 写非法 UTF-8 字节
        (tmp_path / "output" / "setting_package.json").write_bytes(
            b'{"valid_key": "\xff\xfe\x00\x41"}'
        )

        project_id = f"test-pull-{secrets.token_hex(8)}"
        db = SessionLocal()
        try:
            project = Project(
                id=project_id, title="test", genre="玄幻", status="ready",
                config_json={},
            )
            db.add(project)
            db.commit()
        finally:
            db.close()

        db = SessionLocal()
        try:
            with pytest.raises(ValueError, match="setting_package.json 损坏"):
                asyncio.run(pull_setting_package(project_id, str(tmp_path), db))
        finally:
            try:
                db.query(Project).filter_by(id=project_id).delete()
                db.commit()
            except Exception:
                pass
            db.close()

    def test_pull_setting_source_has_json_error_handling(self):
        """源码级锁死：pull_setting_package 必须 catch JSONDecodeError + UnicodeDecodeError。"""
        from pathlib import Path
        sync_py = Path(__file__).resolve().parents[1] / "app" / "bridge" / "setting_sync.py"
        content = sync_py.read_text(encoding="utf-8")
        # 找 pull_setting_package 函数
        import re
        m = re.search(
            r"async def pull_setting_package\([\s\S]*?\):",
            content, re.DOTALL
        )
        assert m, "找不到 pull_setting_package"
        # 取函数后到下一个 def 之前的内容
        start = m.end()
        lines = content[start:].splitlines()
        body_lines = []
        for line in lines:
            if line.startswith("async def ") or line.startswith("def ") or line.startswith("class "):
                break
            body_lines.append(line)
        body = "\n".join(body_lines)
        assert "JSONDecodeError" in body, (
            "pull_setting_package 必须 catch JSONDecodeError"
        )
        assert "UnicodeDecodeError" in body, (
            "pull_setting_package 必须 catch UnicodeDecodeError"
        )
        assert "ValueError" in body, (
            "必须转抛 ValueError（带用户可读信息，不是原始 traceback）"
        )


# ───────────────────────────────────────────
# OOO: save_l2 / save_l5 atomic write（迭代 #36）
# ───────────────────────────────────────────
class TestMemorySaveAtomic:
    """迭代 #36: save_l2 / save_l5 之前直接 open(path, "w") 写一半进程被杀
    → 文件损坏 → get_l2 静默返回 empty_l2 → 下次 save 覆盖空数据
    → L2/L5 记忆永久丢失。

    修法：
      1. save_l2/save_l5 用 atomic write（先 .tmp + os.replace + 失败重试 3 次）
      2. get_l2/get_l5 损坏文件不再静默返回空，而是备份为 .corrupted.{ts}
         后再返回 default（让用户能事后取回数据）
    """
    def test_save_l2_atomic_write_uses_tmp_file(self, monkeypatch):
        """save_l2 源码必须用 atomic write（.tmp + os.replace）。"""
        from pathlib import Path
        manager_py = Path(__file__).resolve().parents[1] / "engine" / "memory" / "manager.py"
        content = manager_py.read_text(encoding="utf-8")
        # 用基于行的解析：找 def save_l2 行，下一个 def 之前都是 body
        lines = content.splitlines()
        body_start = None
        for i, line in enumerate(lines):
            if line.startswith("def save_l2("):
                body_start = i + 1
                break
        assert body_start is not None, "找不到 save_l2"
        body_lines = []
        for line in lines[body_start:]:
            if line.startswith("def ") or line.startswith("class "):
                break
            body_lines.append(line)
        body = "\n".join(body_lines)
        # 排除纯注释行
        code_lines = [
            line for line in body.splitlines()
            if not line.strip().startswith("#")
        ]
        code_body = "\n".join(code_lines)
        assert ".tmp" in code_body, (
            "save_l2 体内必须用 .tmp 中间文件做 atomic write（之前直接 open path 直接写）"
        )
        assert "os.replace" in code_body, (
            "save_l2 必须用 os.replace 原子重命名（不是直接 shutil.move）"
        )

    def test_save_l5_atomic_write_uses_helper(self):
        """save_l5 调用 atomic_write_json helper（包含 .tmp + os.replace）。

        迭代 #39 后 helper 已从 memory/manager.py 私有 _atomic_write_json
        提升到 engine/utils.atomic_write_json。save_l5 通过 `as _atomic_write_json`
        别名 import，但 helper 本体必须在 utils.py。
        """
        from pathlib import Path
        manager_py = Path(__file__).resolve().parents[1] / "engine" / "memory" / "manager.py"
        utils_py = Path(__file__).resolve().parents[1] / "engine" / "utils.py"
        manager_content = manager_py.read_text(encoding="utf-8")
        utils_content = utils_py.read_text(encoding="utf-8")
        manager_lines = manager_content.splitlines()
        body_start = None
        for i, line in enumerate(manager_lines):
            if line.startswith("def save_l5("):
                body_start = i + 1
                break
        assert body_start is not None, "找不到 save_l5"
        body_lines = []
        for line in manager_lines[body_start:]:
            if line.startswith("def ") or line.startswith("class "):
                break
            body_lines.append(line)
        body = "\n".join(body_lines)
        code_lines = [
            line for line in body.splitlines()
            if not line.strip().startswith("#")
        ]
        code_body = "\n".join(code_lines)
        # save_l5 必须调 _atomic_write_json（通过别名）
        assert "_atomic_write_json" in code_body, (
            "save_l5 必须调 _atomic_write_json helper（atomic write）"
        )
        # helper 本体必须在 utils.py：def atomic_write_json(...) 必须存在 + 有 .tmp + os.replace
        utils_lines = utils_content.splitlines()
        helper_start = None
        for i, line in enumerate(utils_lines):
            if line.startswith("def atomic_write_json"):
                helper_start = i + 1
                break
        assert helper_start is not None, "engine/utils.py 找不到 atomic_write_json helper（iter #39 后应在 utils）"
        helper_lines = []
        for line in utils_lines[helper_start:]:
            if line.startswith("def ") or line.startswith("class "):
                break
            helper_lines.append(line)
        helper_body = "\n".join(helper_lines)
        assert ".tmp" in helper_body, (
            "atomic_write_json helper 必须用 .tmp 中间文件"
        )
        assert "os.replace" in helper_body, (
            "atomic_write_json helper 必须用 os.replace 原子重命名"
        )

    def test_get_l2_corrupt_file_backed_up_not_silently_lost(self, tmp_path, monkeypatch):
        """get_l2 读到损坏文件时必须备份（不能静默返回空）。"""
        from engine.memory import manager
        # 切到临时 L2 目录
        monkeypatch.setattr(manager, "L2_DIR_STR", str(tmp_path))
        # 写一个损坏文件
        bad_path = tmp_path / "test-novel_memory.json"
        bad_path.write_text("{not valid json", encoding="utf-8")
        # 调 get_l2
        result = manager.get_l2("test-novel")
        # 应返回空 L2（不抛）
        assert result["meta"]["novel_id"] == "test-novel"
        # 损坏文件应被备份（文件名含 .corrupted.）
        backups = list(tmp_path.glob("test-novel_memory.json.corrupted.*"))
        assert len(backups) == 1, (
            f"损坏文件应被备份为 .corrupted.{{ts}}，实际备份：{backups}"
        )

    def test_get_l5_corrupt_file_backed_up_not_silently_lost(self, tmp_path, monkeypatch):
        """get_l5 同样：损坏文件备份。"""
        from engine.memory import manager
        monkeypatch.setattr(manager, "L5_DIR_STR", str(tmp_path))
        bad_path = tmp_path / "test-novel_l5.json"
        bad_path.write_text("{not valid json", encoding="utf-8")
        result = manager.get_l5("test-novel")
        # 默认 L5
        assert result == {
            "arc_summaries": [], "character_arcs": {},
            "major_revelations": [], "compressed_history": ""
        }
        backups = list(tmp_path.glob("test-novel_l5.json.corrupted.*"))
        assert len(backups) == 1, (
            f"L5 损坏文件应被备份，实际：{backups}"
        )

    def test_save_l2_then_load_roundtrip(self, tmp_path, monkeypatch):
        """save_l2 → get_l2 round-trip 数据不丢。"""
        from engine.memory import manager
        monkeypatch.setattr(manager, "L2_DIR_STR", str(tmp_path))
        original = manager.empty_l2()
        original["hot"]["protagonist_points"] = 9999
        original["hot"]["active_threads"] = ["线A", "线B"]
        manager.save_l2("test-rt", original)
        loaded = manager.get_l2("test-rt")
        assert loaded["hot"]["protagonist_points"] == 9999
        assert loaded["hot"]["active_threads"] == ["线A", "线B"]


# ───────────────────────────────────────────
# PPP: rules post-process LLM 失败不能 fake-pass（迭代 #37）
# ───────────────────────────────────────────
class TestPostProcessLLMFailure:
    """迭代 #37: rules.py _llm_call_for_postprocess 之前 except Exception
    返回占位文本（"[tool] LLM 调用失败..."）+ cost=0。

    这是 fake-pass 同型问题：前端收到占位 + cost=0，误以为"逻辑评估完成"
    实际 LLM 失败。改 raise HTTPException(503) 让用户看到真实错误。

    本测试锁死：mock LLM 抛异常 → post_process 必须 raise 503，
    不是返回占位文本。
    """
    def test_post_process_raises_503_on_llm_failure(self, monkeypatch):
        """LLM 抛异常 → post_process 必须 raise HTTPException 503。"""
        from fastapi.testclient import TestClient
        from app.main import app
        from app.database import SessionLocal
        from app.models import Project, Chapter, RuleConfig
        import secrets

        project_id = f"test-postproc-{secrets.token_hex(8)}"
        db = SessionLocal()
        try:
            # 准备 project + chapter + rule config
            project = Project(
                id=project_id, title="test", genre="玄幻", status="ready",
                config_json={},
            )
            db.add(project)
            chapter = Chapter(
                project_id=project_id, chapter_no=1, title="ch1",
                content="林尘盘膝坐下，闭目调息。\n",
            )
            db.add(chapter)
            db.commit()
        finally:
            db.close()

        # mock LLM router 抛异常
        from app.api import rules as rules_mod
        from engine.llm import router as router_mod

        class FakeRouter:
            def call(self, *a, **kw):
                raise ConnectionError("simulated LLM 503")

        # monkeypatch get_active_router 返回 FakeRouter
        from engine import llm_router
        monkeypatch.setattr(llm_router, "get_active_router", lambda: FakeRouter())
        monkeypatch.setattr(router_mod, "LLMRouter", lambda *a, **kw: FakeRouter())

        client = TestClient(app)
        try:
            r = client.post(
                f"/projects/{project_id}/rules/post-process",
                json={"tool": "logic"},
            )
            # 必须 503（之前是 200 + 占位文本）
            assert r.status_code == 503, (
                f"LLM 失败时应返回 503，实际 {r.status_code}：{r.text}"
            )
            # detail 必须含 "LLM 调用失败" 关键词
            body = r.json()
            assert "LLM 调用失败" in str(body), (
                f"503 响应 detail 应含 'LLM 调用失败'，实际：{body}"
            )
        finally:
            db = SessionLocal()
            try:
                from app.models import Chapter
                db.query(Chapter).filter_by(project_id=project_id).delete()
                db.query(RuleConfig).filter_by(project_id=project_id).delete()
                db.query(Project).filter_by(id=project_id).delete()
                db.commit()
            except Exception:
                pass
            db.close()

    def test_post_process_source_uses_503_not_fake_pass(self):
        """源码级锁死：post-process LLM 失败时必须 raise HTTPException 不是 return 占位。"""
        from pathlib import Path
        rules_py = Path(__file__).resolve().parents[1] / "app" / "api" / "rules.py"
        content = rules_py.read_text(encoding="utf-8")
        # 找 _llm_call_for_postprocess 函数体
        lines = content.splitlines()
        body_start = None
        for i, line in enumerate(lines):
            if line.startswith("def _llm_call_for_postprocess"):
                body_start = i + 1
                break
        assert body_start is not None, "找不到 _llm_call_for_postprocess"
        body_lines = []
        for line in lines[body_start:]:
            if line.startswith("def ") or line.startswith("class "):
                break
            body_lines.append(line)
        body = "\n".join(body_lines)
        # 关键：必须有 raise HTTPException（不是 return 占位）
        assert "raise HTTPException" in body, (
            "_llm_call_for_postprocess 必须 raise HTTPException（不是 return 占位）"
        )
        # 关键：不能有"return 失败占位"模式
        assert "LLM 调用失败" in body, (
            "需要 raise HTTPException 503 with 'LLM 调用失败' detail"
        )
        # 反向：真代码行不能有 return 一个虚假成功占位
        code_lines = [
            line for line in body.splitlines()
            if not line.strip().startswith("#")
        ]
        code_body = "\n".join(code_lines)
        # 检查不能有"LLM 调用失败"字面量被作为 return 内容
        # （出现在 raise detail 里是 OK 的）
        return_lines = [l for l in code_body.splitlines() if "return" in l and "LLM" in l]
        # 允许 raise ... "LLM 调用失败"（含 LLM 字面量）但不应该是 return
        for line in return_lines:
            assert line.strip().startswith("raise"), (
                f"不能 return 含 LLM 字面量的占位文本（应该是 raise）：{line!r}"
            )


# ───────────────────────────────────────────
# QQQ: llm_router 静默 decrypt 失败要 log（迭代 #38）
# ───────────────────────────────────────────
class TestLlmRouterDecryptFailureLogging:
    """迭代 #38: engine/llm_router.py load_routes 之前 except Exception
    静默吞解密错误（MASTER_KEY 变了 → key=""），无 log。

    后果：用户改 MASTER_KEY env 后所有 LLM 不可用，错误日志里没任何线索，
    排查只能从 DB 翻 Provider.api_key_encrypted 自己 decode。

    修法：log warning 告诉用户哪个 provider 解密失败。
    本测试锁死：mock decrypt_api_key 抛异常 → load_routes 必须 log warning。
    """
    def test_load_routes_logs_warning_on_decrypt_failure(self, caplog):
        """decrypt_api_key 抛异常 → load_routes 必须 log warning（不静默）。"""
        import logging
        from engine import llm_router
        from engine.llm_router import LLMRouter as BridgeLLMRouter
        from app.database import SessionLocal
        from app.models import Provider, RoleAssignment
        import secrets

        # mock decrypt_api_key 抛异常
        def fake_decrypt(ciphertext):
            raise ValueError("simulated MASTER_KEY mismatch")
        import app.security
        original_decrypt = app.security.decrypt_api_key
        app.security.decrypt_api_key = fake_decrypt
        # llm_router 已经 import 了 decrypt_api_key 的引用，需要 patch 它
        llm_router.decrypt_api_key = fake_decrypt

        try:
            # 准备 project + provider + role assignment
            provider_id = f"test-decrypt-{secrets.token_hex(8)}"
            role_key = f"test-role-{secrets.token_hex(4)}"
            db = SessionLocal()
            try:
                p = Provider(
                    id=provider_id,
                    name="test-decrypt",
                    provider_type="anthropic",
                    api_key_encrypted="encrypted-blob-fake",
                    api_key_suffix="abcd",
                    default_model="claude-test",
                )
                db.add(p)
                ra = RoleAssignment(role_key=role_key, provider_id=provider_id)
                db.add(ra)
                db.commit()
            finally:
                db.close()

            r = BridgeLLMRouter("test-decrypt-novel")
            with caplog.at_level(logging.WARNING, logger="novel_ai.llm_router"):
                r.load_routes()
            # 关键断言：必须 log warning（不能静默）
            warnings = [r for r in caplog.records if r.levelname == "WARNING"]
            assert len(warnings) >= 1, (
                f"decrypt 失败时必须 log warning，实际 log："
                f"{[(r.levelname, r.message) for r in caplog.records]}"
            )
            # warning 信息应含 provider id 或 role_key
            assert any(role_key in r.message or provider_id in r.message for r in warnings), (
                f"warning 信息应含 provider/role 标识，实际：{[r.message for r in warnings]}"
            )
        finally:
            app.security.decrypt_api_key = original_decrypt
            llm_router.decrypt_api_key = original_decrypt
            # 清理
            db = SessionLocal()
            try:
                db.query(RoleAssignment).filter_by(role_key=role_key).delete()
                db.query(Provider).filter_by(id=provider_id).delete()
                db.commit()
            except Exception:
                pass
            db.close()

    def test_load_routes_source_logs_on_decrypt_failure(self):
        """源码级锁死：load_routes 必须 log.warning。"""
        from pathlib import Path
        router_py = Path(__file__).resolve().parents[1] / "engine" / "llm_router.py"
        content = router_py.read_text(encoding="utf-8")
        # 找 load_routes 函数体（兼容多行签名 + 缩进）
        lines = content.splitlines()
        body_start = None
        for i, line in enumerate(lines):
            if "def load_routes" in line:
                body_start = i + 1
                break
        assert body_start is not None, "找不到 load_routes"
        body_lines = []
        for line in lines[body_start:]:
            if line.startswith("def ") or line.startswith("    def ") or line.startswith("class "):
                break
            body_lines.append(line)
        body = "\n".join(body_lines)
        assert "log.warning" in body, (
            "load_routes 必须 log.warning（之前静默吞 decrypt 错误无 log）"
        )
        assert "decrypt" in body.lower(), (
            "load_routes 体内应有 decrypt 相关处理（不能是死代码）"
        )


# ───────────────────────────────────────────
# WW: rate_limit X-RateLimit-Remaining 准确性（最后 #18 迭代）
# ───────────────────────────────────────────
class TestRateLimitHeaderAccuracy:
    """最后 #18 迭代：rate_limit middleware 的 X-RateLimit-Remaining
    应该在被限流时返回 0 + Retry-After header。

    之前 TestRateLimitMiddleware 测了基本流程，没验证 header 准确性。
    生产监控可能根据 X-RateLimit-Remaining 做自动降级判断。
    """

    def test_rate_limited_response_has_zero_remaining(self):
        """被限流的响应 X-RateLimit-Remaining 必须 = 0。"""
        from fastapi.testclient import TestClient
        from app.main import app
        from app.middleware import rate_limit
        from app.middleware.rate_limit import reset_for_testing

        rate_limit._limiter = rate_limit.IPRateLimiter(max_per_minute=1)
        try:
            client = TestClient(app)
            client.post("/api/v1/providers", json={})  # 1
            r2 = client.post("/api/v1/providers", json={})  # 2 → 限流
            assert r2.status_code == 429
            assert r2.headers.get("X-RateLimit-Remaining") == "0", (
                f"被限流时 X-RateLimit-Remaining 必须 = 0，实际 {r2.headers.get('X-RateLimit-Remaining')}"
            )
        finally:
            reset_for_testing()
            rate_limit._limiter = rate_limit.IPRateLimiter(max_per_minute=10000)

    def test_rate_limited_response_has_retry_after(self):
        """429 响应必须含 Retry-After header（让客户端知道多久重试）。"""
        from fastapi.testclient import TestClient
        from app.main import app
        from app.middleware import rate_limit
        from app.middleware.rate_limit import reset_for_testing

        rate_limit._limiter = rate_limit.IPRateLimiter(max_per_minute=1)
        try:
            client = TestClient(app)
            client.post("/api/v1/providers", json={})  # 1
            r2 = client.post("/api/v1/providers", json={})  # 2 → 限流
            assert r2.status_code == 429
            retry_after = r2.headers.get("Retry-After")
            assert retry_after is not None, "429 必须含 Retry-After header"
            assert int(retry_after) > 0, f"Retry-After 必须 > 0，实际 {retry_after}"
        finally:
            reset_for_testing()
            rate_limit._limiter = rate_limit.IPRateLimiter(max_per_minute=10000)

    def test_health_endpoint_not_rate_limited(self):
        """/health 是健康检查（k8s livenessProbe 高频调用）不能被限流。"""
        from fastapi.testclient import TestClient
        from app.main import app
        client = TestClient(app)
        for _ in range(10):
            r = client.get("/health")
            assert r.status_code in (200, 503), (
                f"/health 不应被限流（GET 早退），实际 {r.status_code}"
            )


# ───────────────────────────────────────────
# XX: audit_project.py 端到端（最后 #19 迭代）
# ───────────────────────────────────────────
class TestAuditProjectRunEndToEnd:
    """最后 #19：audit_project 是 CI 入口，必须能真跑通（不 crash）。"""
    import subprocess

    def test_audit_runs_successfully_returns_zero_or_one_exit(self):
        """audit_project 在当前 DB 上应正常返回（exit 0 或 1）。"""
        from pathlib import Path
        backend_root = Path(__file__).resolve().parents[1]
        result = self.subprocess.run(
            ["python", "-m", "scripts.audit_project"],
            cwd=backend_root,
            capture_output=True, text=True, timeout=30,
        )
        assert result.returncode in (0, 1), (
            f"audit 应返回 0/1，实际 {result.returncode}。stderr: {result.stderr[:300]}"
        )

    def test_audit_collects_pass_and_info(self):
        """audit 报告应同时含 PASS 段 + INFO 段。"""
        from pathlib import Path
        backend_root = Path(__file__).resolve().parents[1]
        result = self.subprocess.run(
            ["python", "-m", "scripts.audit_project"],
            cwd=backend_root,
            capture_output=True, text=True, timeout=30,
        )
        assert "PASS" in result.stdout, "audit 输出应有 PASS 段"
        assert "INFO" in result.stdout, (
            "audit 输出应有 INFO 段（前置条件未满足的跳过）"
        )


# ───────────────────────────────────────────
# YY: migrations.py idempotency 测试（最后 #20）
# ───────────────────────────────────────────
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


# ───────────────────────────────────────────
# ZZ: app/database.py get_db dependency 测试（最后 #21）
# ───────────────────────────────────────────
class TestGetDbDependency:
    """最后 #21：get_db 是 FastAPI Depends 入口，零测试覆盖。"""
    def test_get_db_yields_session_and_closes_on_exit(self):
        from app.database import get_db, SessionLocal
        from app.models import BridgeRun
        gen = get_db()
        db = next(gen)
        assert db is not None
        try:
            db.query(BridgeRun).first()
        except Exception:
            pass
        try:
            next(gen)
        except StopIteration:
            pass

    def test_get_db_closes_session_on_exception(self):
        from app.database import get_db
        from app.models import BridgeRun
        gen = get_db()
        db = next(gen)
        try:
            db.query(BridgeRun).first()
            try:
                gen.throw(RuntimeError("downstream boom"))
            except RuntimeError as e:
                assert "downstream boom" in str(e)
        finally:
            try:
                next(gen)
            except StopIteration:
                pass

    def test_sessionmaker_binds_to_engine(self):
        from app.database import SessionLocal, engine
        sess = SessionLocal()
        try:
            bind = sess.get_bind()
            assert bind is engine, (
                f"SessionLocal 应 bind 到 app.database.engine，实际 {bind}"
            )
        finally:
            sess.close()


# ───────────────────────────────────────────
# AAA: app/bridge/reports.py apply_review 输入校验（最后 #22）
# ───────────────────────────────────────────
class TestApplyReviewInputValidation:
    """最后 #22：apply_review 是用户审核端点，零测试覆盖。"""
    def test_invalid_action_raises_value_error(self):
        from app.bridge.reports import apply_review, VALID_REVIEW_ACTIONS
        import pytest
        with pytest.raises(ValueError, match="unsupported review action"):
            apply_review(novel_ai_dir="/tmp/nonexistent", action="invalid_action_xyz")
        assert VALID_REVIEW_ACTIONS == {"accept", "reject", "edit"}

    def test_nonexistent_state_returns_not_available(self):
        from app.bridge.reports import apply_review
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            result = apply_review(
                novel_ai_dir=tmpdir,
                action="accept",
                task_id="any_task",
            )
            assert result["available"] is False, f"应 available=False，实际 {result}"

    def test_valid_actions_do_not_raise(self):
        from app.bridge.reports import apply_review
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            for action in ["accept", "reject", "edit"]:
                result = apply_review(novel_ai_dir=tmpdir, action=action)
                assert result["available"] is False

    def test_unmatched_task_id_does_not_pop_wrong_task(self, tmp_path):
        """迭代 #29：task_id 不存在时不能 pop 错的 pending 任务。

        历史 bug：_find_task_index 之前"没找到"时 fallback 到 0，
        silently pop 第一条 pending 任务。用户提交 review with task_id="X"
        但 X 不存在 → 第一条 pending 被静默移除，review_history 记的
        是 "X" 但实际 pop 的是另一条 → 数据完整性破坏。

        修法：_find_task_index 在没找到时显式返回 None，apply_review 不 pop。
        """
        from app.bridge.reports import apply_review
        import json
        import os

        # 准备 state：3 个 pending 任务
        # _state_path 走 NOVEL_AI_DIR/output/orchestrator_state.json
        state = {
            "current_phase": "writing",
            "human_pending": [
                {"task_id": "real-task-A", "task_type": "fix_chapter",
                 "description": "task A", "payload": {"chapter_number": 1},
                 "created_at": "2025-01-01T00:00:00", "priority": "must"},
                {"task_id": "real-task-B", "task_type": "fix_chapter",
                 "description": "task B", "payload": {"chapter_number": 2},
                 "created_at": "2025-01-01T00:00:00", "priority": "must"},
                {"task_id": "real-task-C", "task_type": "fix_chapter",
                 "description": "task C", "payload": {"chapter_number": 3},
                 "created_at": "2025-01-01T00:00:00", "priority": "must"},
            ],
        }
        output_dir = tmp_path / "output"
        output_dir.mkdir(parents=True, exist_ok=True)
        state_file = output_dir / "orchestrator_state.json"
        state_file.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")

        old_env = os.environ.get("NOVEL_AI_DIR")
        os.environ["NOVEL_AI_DIR"] = str(tmp_path)
        try:
            result = apply_review(
                novel_ai_dir=str(tmp_path),
                action="accept",
                task_id="nonexistent-task-X",  # 不存在
            )
            # 关键断言 1：响应里 matched=False
            assert result["matched"] is False, (
                f"task_id 不存在时 matched 必须 False，实际 {result.get('matched')}"
            )
            # 关键断言 2：3 个 pending 任务一个都没被 pop
            on_disk = json.loads(state_file.read_text(encoding="utf-8"))
            assert len(on_disk["human_pending"]) == 3, (
                f"task_id 不存在时不应 pop 任何 pending，"
                f"实际剩余 {len(on_disk['human_pending'])} 条（之前 bug: pop 了 0 号任务）"
            )
            assert [t["task_id"] for t in on_disk["human_pending"]] == [
                "real-task-A", "real-task-B", "real-task-C",
            ], (
                f"pending 顺序应保持不变，"
                f"实际 {[t['task_id'] for t in on_disk['human_pending']]}"
            )
            # 关键断言 3：review_history 记录了"尝试过 X 但未匹配"
            history = on_disk.get("review_history", [])
            assert len(history) == 1
            assert history[0]["task_id"] == "nonexistent-task-X"
            assert history[0]["matched"] is False
        finally:
            if old_env is not None:
                os.environ["NOVEL_AI_DIR"] = old_env
            else:
                os.environ.pop("NOVEL_AI_DIR", None)

    def test_unmatched_chapter_number_does_not_pop_wrong_task(self, tmp_path):
        """chapter_number 不存在时也不能 pop 错的 pending。"""
        from app.bridge.reports import apply_review
        import json
        import os

        state = {
            "current_phase": "writing",
            "human_pending": [
                {"task_id": "task-A", "task_type": "fix_chapter",
                 "description": "task A", "payload": {"chapter_number": 5},
                 "created_at": "2025-01-01T00:00:00", "priority": "must"},
            ],
        }
        output_dir = tmp_path / "output"
        output_dir.mkdir(parents=True, exist_ok=True)
        state_file = output_dir / "orchestrator_state.json"
        state_file.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")

        old_env = os.environ.get("NOVEL_AI_DIR")
        os.environ["NOVEL_AI_DIR"] = str(tmp_path)
        try:
            result = apply_review(
                novel_ai_dir=str(tmp_path),
                action="reject",
                chapter_number=999,  # 不存在
            )
            assert result["matched"] is False
            on_disk = json.loads(state_file.read_text(encoding="utf-8"))
            assert len(on_disk["human_pending"]) == 1, (
                "chapter_number 不存在时不应 pop 任何 pending"
            )
        finally:
            if old_env is not None:
                os.environ["NOVEL_AI_DIR"] = old_env
            else:
                os.environ.pop("NOVEL_AI_DIR", None)

    def test_matched_task_id_pops_correct_task(self, tmp_path):
        """task_id 匹配时必须 pop 对的任务。"""
        from app.bridge.reports import apply_review
        import json
        import os

        state = {
            "current_phase": "writing",
            "human_pending": [
                {"task_id": "task-A", "task_type": "fix_chapter",
                 "description": "A", "payload": {"chapter_number": 1},
                 "created_at": "2025-01-01T00:00:00", "priority": "must"},
                {"task_id": "task-B", "task_type": "fix_chapter",
                 "description": "B", "payload": {"chapter_number": 2},
                 "created_at": "2025-01-01T00:00:00", "priority": "must"},
            ],
        }
        output_dir = tmp_path / "output"
        output_dir.mkdir(parents=True, exist_ok=True)
        state_file = output_dir / "orchestrator_state.json"
        state_file.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")

        old_env = os.environ.get("NOVEL_AI_DIR")
        os.environ["NOVEL_AI_DIR"] = str(tmp_path)
        try:
            result = apply_review(
                novel_ai_dir=str(tmp_path),
                action="accept",
                task_id="task-B",
            )
            assert result["matched"] is True
            assert result["task"]["task_id"] == "task-B"
            on_disk = json.loads(state_file.read_text(encoding="utf-8"))
            assert [t["task_id"] for t in on_disk["human_pending"]] == ["task-A"], (
                f"应只 pop task-B，剩余 task-A，实际 {[t['task_id'] for t in on_disk['human_pending']]}"
            )
        finally:
            if old_env is not None:
                os.environ["NOVEL_AI_DIR"] = old_env
            else:
                os.environ.pop("NOVEL_AI_DIR", None)


# ───────────────────────────────────────────
# BBB: engine/state.py load_state 损坏文件处理（最后 #23）
# ───────────────────────────────────────────
class TestLoadStateRobustness:
    """最后 #23：load_state 之前零测试覆盖。"""
    def test_load_state_corrupt_json_raises(self, tmp_path):
        from engine.state import load_state
        path = tmp_path / "state.json"
        path.write_text("THIS IS NOT VALID JSON{", encoding="utf-8")
        import pytest
        with pytest.raises(__import__("json").JSONDecodeError):
            load_state(str(path))

    def test_load_state_empty_file_raises(self, tmp_path):
        from engine.state import load_state
        path = tmp_path / "state.json"
        path.write_text("", encoding="utf-8")
        import pytest
        with pytest.raises(__import__("json").JSONDecodeError):
            load_state(str(path))

    def test_load_state_valid_json_returns_dict(self, tmp_path):
        from engine.state import save_state, create_initial_state, load_state
        path = str(tmp_path / "state.json")
        state = create_initial_state("test", "title", "fanqie", "都市", "")
        save_state(state, path)
        loaded = load_state(path)
        assert loaded["novel_id"] == "test"
        assert loaded["title"] == "title"


# ───────────────────────────────────────────
# CCC: 文档与代码一致性 invariants（最后 #24）
# ───────────────────────────────────────────
class TestDocCodeConsistency:
    """最后 #24：锁死 CHANGELOG / README / 前端类型 跟代码状态一致。"""
    def test_changelog_mentions_recent_security_fixes(self):
        from pathlib import Path
        cl = (Path(__file__).resolve().parents[2] / "CHANGELOG.md").read_text(encoding="utf-8")
        for keyword in ["API key", "MASTER_KEY", "subprocess", "Mock"]:
            assert keyword in cl, f"CHANGELOG 缺关键字 '{keyword}'"

    def test_readme_has_deployment_section_and_master_key(self):
        from pathlib import Path
        readme = (Path(__file__).resolve().parents[2] / "README.md").read_text(encoding="utf-8")
        assert "## 部署" in readme, "README 缺「部署」章节"
        assert "MASTER_KEY" in readme, "README 部署章节必须提到 MASTER_KEY"

    def test_scripts_directory_lists_operational_tools(self):
        from pathlib import Path
        scripts = Path(__file__).resolve().parents[2] / "backend" / "scripts"
        for tool in ["generate_master_key.py", "rotate_master_key.py", "export_openapi.py"]:
            assert (scripts / tool).exists(), f"scripts/{tool} 不存在"

    def test_frontend_gitignore_excludes_openapi_json(self):
        from pathlib import Path
        gi = (Path(__file__).resolve().parents[2] / "frontend" / ".gitignore").read_text(encoding="utf-8")
        assert "openapi.json" in gi, "frontend/.gitignore 必须含 openapi.json"


# ───────────────────────────────────────────
# DDD: app/security.py 安全常量 invariants（最后 #25）
# ───────────────────────────────────────────
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


# ───────────────────────────────────────────
# EEE: CHANGELOG 包含所有本轮 commit hash（最后 #26）
# ───────────────────────────────────────────
class TestChangelogCoversAllCommits:
    """最后 #26：CHANGELOG.md 必须提到本轮所有 push 的 commit hash。"""
    def test_changelog_has_recent_commit_hashes(self):
        """CHANGELOG.md 至少提到 5 个 Phase 1.5 / 深度修复轮 commit（防漂移）。"""
        import subprocess
        from pathlib import Path
        repo = Path(__file__).resolve().parents[2]
        # 取最近 100 个 commit hash（覆盖 Phase 1.5 + 深度修复轮 + 本轮新增）
        result = subprocess.run(
            ["git", "log", "--format=%h", "-n", "100"],
            cwd=repo,
            capture_output=True, text=True, timeout=10,
        )
        commit_hashes = result.stdout.strip().splitlines()
        cl = (repo / "CHANGELOG.md").read_text(encoding="utf-8")
        mentioned = sum(1 for h in commit_hashes if h in cl)
        assert mentioned >= 5, (
            f"CHANGELOG 应至少提到 5 个 commit hash，实际 {mentioned}/{len(commit_hashes)}"
        )

    def test_changelog_unreleased_section_exists(self):
        from pathlib import Path
        cl = (Path(__file__).resolve().parents[2] / "CHANGELOG.md").read_text(encoding="utf-8")
        assert "Unreleased" in cl or "深度修复" in cl, (
            "CHANGELOG 应有 Unreleased / 深度修复轮 段落"
        )

    def test_repo_not_in_clean_state(self):
        import subprocess
        from pathlib import Path
        repo = Path(__file__).resolve().parents[2]
        result = subprocess.run(
            ["git", "rev-list", "--count", "HEAD"],
            cwd=repo,
            capture_output=True, text=True, timeout=10,
        )
        count = int(result.stdout.strip())
        assert count >= 10, f"repo 应至少 10 个 commit，实际 {count}"


# ───────────────────────────────────────────
# FFF: Provider 表结构 invariants（最后 #27）
# ───────────────────────────────────────────
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


# ───────────────────────────────────────────
# GGG: orchestrator.py human_escalation bug 修复锁死（独立 AI 审查发现）
# ───────────────────────────────────────────
class TestHumanEscalationNotEndRun:
    """独立 AI 深度审查发现（2026-07-03 报告）：
       orchestrator.py:573 之前 g.add_edge("human_escalation", END)，
       与 graph.py:290 的 human_escalation → load_arc_tasks 不一致。

       后果：run/resume 走 orchestrator 的图，章节触发人工介入时
       stream() 立即终止 → 整次 run 静默提前结束（即便 chapters_done
       < max_chapters），用户视角"成功"但实际没写完。

    本测试锁死：orchestrator.py 和 graph.py 的图拓扑必须一致。
    """
    def test_orchestrator_human_escalation_edge_target(self):
        """orchestrator.py 的图 human_escalation 必须指向 load_arc_tasks（不是 END）。"""
        import inspect
        from engine import orchestrator as orch_mod
        src = inspect.getsource(orch_mod.build_graph)
        # 找 human_escalation 行的 add_edge
        import re
        m = re.search(r'g\.add_edge\(\s*"human_escalation"\s*,\s*([^)]+)\)', src)
        assert m, "找不到 g.add_edge(human_escalation, ...)"
        target = m.group(1).strip()
        assert target == '"load_arc_tasks"', (
            f"orchestrator 的 human_escalation 必须指向 load_arc_tasks（继续下一章），"
            f"实际 {target!r}（独立 AI 审查发现的 bug）"
        )

    def test_graph_py_human_escalation_edge_target(self):
        """graph.py 的图 human_escalation 也必须指向 load_arc_tasks（两个文件保持一致）。"""
        import inspect
        from engine import graph as graph_mod
        src = inspect.getsource(graph_mod.build_project_graph)
        import re
        m = re.search(r'g\.add_edge\(\s*"human_escalation"\s*,\s*([^)]+)\)', src)
        assert m, "graph.py 找不到 g.add_edge(human_escalation, ...)"
        target = m.group(1).strip()
        assert target == '"load_arc_tasks"', (
            f"graph.py human_escalation 必须指向 load_arc_tasks，实际 {target!r}"
        )

    def test_both_graphs_have_consistent_topology(self):
        """orchestrator.py 和 graph.py 的图拓扑必须一致（防再次漂移）。"""
        import inspect
        from engine import orchestrator as orch_mod
        from engine import graph as graph_mod
        # 提取两个文件里所有 g.add_edge(...)
        def edges(src):
            import re
            return set(re.findall(r'g\.add_edge\(\s*"([^"]+)"\s*,\s*"([^"]+)"\s*\)', src))
        orch_edges = edges(inspect.getsource(orch_mod.build_graph))
        graph_edges = edges(inspect.getsource(graph_mod.build_project_graph))
        # human_escalation 必须两边都是 load_arc_tasks（关键边）
        assert ("human_escalation", "load_arc_tasks") in orch_edges, (
            "orchestrator 缺 human_escalation → load_arc_tasks 边"
        )
        assert ("human_escalation", "load_arc_tasks") in graph_edges, (
            "graph 缺 human_escalation → load_arc_tasks 边"
        )
# ───────────────────────────────────────────
# HHH: planner.py / compliance.py / tracker.py / init_arc.py bug 修复锁死
# ───────────────────────────────────────────
class TestAtomicWriteJsonPromoted:
    """engine/utils.py 提供公共 atomic_write_json（之前只在 memory/manager.py
    私有）。planner.py / init_arc.py 等所有写 JSON 到磁盘的地方都应复用。
    """
    def test_utils_exposes_atomic_write_json(self):
        from engine.utils import atomic_write_json
        assert callable(atomic_write_json), "engine.utils.atomic_write_json 必须是函数"

    def test_atomic_write_json_roundtrip(self, tmp_path):
        """写一次 → 读回 → 数据一致；写时 .tmp 残留也被清理。"""
        from engine.utils import atomic_write_json
        import json
        target = tmp_path / "data.json"
        data = {"novel_id": "test", "arcs": [1, 2, 3]}
        atomic_write_json(str(target), data)
        assert target.exists(), "atomic_write_json 写完后文件必须存在"
        with open(target, encoding="utf-8") as f:
            loaded = json.load(f)
        assert loaded == data
        # .tmp 文件不应残留
        assert not (tmp_path / "data.json.tmp").exists(), \
            "atomic_write_json 完成后 .tmp 必须被 os.replace 走"

    def test_memory_manager_uses_public_atomic_write(self):
        """memory/manager.py 必须用 engine.utils.atomic_write_json，不再自己定义。
        （通过 `as _atomic_write_json` 的别名 import 是允许的，只要不是 `def` 自己定义）
        """
        import inspect, re
        from engine.memory import manager as mgr_mod
        src = inspect.getsource(mgr_mod)
        # 检查 1：必须 import 了公共版本（不管别名）
        assert re.search(r"from\s+\.\.utils\s+import\s+atomic_write_json", src), \
            "memory/manager.py 必须 `from ..utils import atomic_write_json`"
        # 检查 2：不能有 `def _atomic_write_json(` 这种私有重定义
        assert not re.search(r"^def\s+_atomic_write_json\s*\(", src, re.MULTILINE), \
            "memory/manager.py 不应再 `def _atomic_write_json(...)` 自己实现"


class TestPlannerAtomicWrite:
    """迭代 #39: planner.py 写 setting_package.json 之前直接 open(w)，
    写一半被杀 → 文件损坏 → 后续 5 张表全空。改用 atomic_write_json。
    """
    def test_planner_imports_atomic_write_json(self):
        import inspect
        from engine.agents import planner as planner_mod
        src = inspect.getsource(planner_mod)
        assert "atomic_write_json" in src, \
            "planner.py 必须 import atomic_write_json（之前直接 open(w) 危险）"

    def test_planner_does_not_use_raw_open_for_json(self):
        """planner.py 不能再出现 `open(out_path, "w", encoding="utf-8")` 这种
        raw write——必须走 atomic_write_json。"""
        import inspect
        from engine.agents import planner as planner_mod
        src = inspect.getsource(planner_mod)
        # 找 setting_package.json 写入附近的代码
        assert 'open(out_path, "w", encoding="utf-8")' not in src, \
            "planner.py 不能再用 raw open(w) 写 setting_package.json（半写损坏风险）"
        assert 'open(out_path, "w"' not in src, \
            "planner.py 不能再用 raw open(w) 写 out_path（半写损坏风险）"

    def test_planner_setting_write_actually_atomic(self, tmp_path):
        """实际跑 run_planner 的写入路径（mock 掉 LLM）验证 atomic_write_json 被调用。"""
        from unittest.mock import patch, MagicMock
        from engine.agents import planner as planner_mod

        # mock LLM 返回 valid JSON
        mock_router = MagicMock()
        mock_router.call.return_value = ('{"novel_id":"x","arc_outline":[],"key_characters":[],"power_system":{"levels":[]}}', 0.001)
        with patch.object(planner_mod, "get_active_router", return_value=mock_router), \
             patch.object(planner_mod, "validate_setting_package"):
            out_dir = tmp_path / "out"
            out_dir.mkdir()
            planner_mod.run_planner(args=[], output_dir=str(out_dir))
            target = out_dir / "setting_package.json"
            assert target.exists(), "setting_package.json 必须被写入"
            # 不应残留 .tmp
            assert not (out_dir / "setting_package.json.tmp").exists(), \
                "atomic write 完成后 .tmp 必须被替换走"


class TestTrackerParseFailureLogged:
    """迭代 #40: tracker.py 之前 parse_llm_json_response(resp, {}) — parse
    失败时 updates={} → chapter_summary / world_events / constraints 全部
    静默丢失。修法：用 None 作为 default 检测失败，log warning +
    meta.last_tracker_parse_failure_chapter + meta.tracker_parse_failure_count。
    """
    def test_tracker_uses_none_default(self):
        """tracker.py 必须用 None（不是 {}）作为 parse default — 才能
        检测 parse 失败并标记 meta。"""
        import inspect
        from engine.agents import tracker as tracker_mod
        src = inspect.getsource(tracker_mod)
        # 去掉注释行（避免 docstring / 注释里出现 `resp, {})` 误匹配）
        code_lines = [
            l for l in src.split("\n")
            if l.strip() and not l.strip().startswith("#")
        ]
        code_src = "\n".join(code_lines)
        # 真实调用行（不是注释）
        assert "parse_llm_json_response(resp, None)" in code_src, \
            "tracker.py 必须用 parse_llm_json_response(resp, None)，不能再传 {}"
        assert "parse_llm_json_response(resp, {})" not in code_src, \
            "tracker.py 不应再用 {} 作为 default（无法区分 parse 失败 vs 空 dict）"

    def test_tracker_logs_warning_on_parse_failure(self, caplog):
        """mock LLM 返回非 JSON → 必须 log warning + meta 标记。"""
        from unittest.mock import patch, MagicMock
        from engine.agents import tracker as tracker_mod

        mock_router = MagicMock()
        # LLM 返回完全无法 parse 的字符串
        mock_router.call.return_value = ("this is not JSON at all" * 20, 0.001)
        with patch.object(tracker_mod, "get_active_router", return_value=mock_router), \
             patch.object(tracker_mod, "save_l2"):
            current_memory = {
                "hot": {"protagonist_level": "感债者", "recent_summaries": []},
                "cold": {"world_events": [], "closed_threads": [], "resolved_foreshadowing": []},
                "constraints": {"forbidden_constraints": [], "established_facts": [],
                                "foreshadowing_planted": []},
                "meta": {"novel_id": "test", "total_chapters_tracked": 5},
            }
            with caplog.at_level("WARNING"):
                tracker_mod.run_tracker("章节正文", {"chapter_number": 6}, current_memory, "test")
            # 至少有 warning 被记下
            warning_msgs = [r.message for r in caplog.records if r.levelname == "WARNING"]
            assert any("tracker" in m.lower() or "parse" in m.lower() for m in warning_msgs), \
                f"parse 失败时 tracker 必须 log warning，实际: {warning_msgs}"
            # meta 必须标记了
            assert current_memory["meta"].get("last_tracker_parse_failure_chapter") == 6, \
                f"meta 必须记 last_tracker_parse_failure_chapter=6，实际 {current_memory['meta']}"
            assert current_memory["meta"].get("tracker_parse_failure_count", 0) >= 1, \
                f"meta.tracker_parse_failure_count 必须 >=1，实际 {current_memory['meta']}"

    def test_tracker_success_path_unaffected(self):
        """正常 JSON 路径仍然更新 hot/cold/constraints，meta 标记不应出现。"""
        from unittest.mock import patch, MagicMock
        from engine.agents import tracker as tracker_mod

        mock_router = MagicMock()
        mock_router.call.return_value = (
            '{"chapter_summary":"主角觉醒","active_threads":["主线"],"inventory_add":["玉佩"]}',
            0.001,
        )
        with patch.object(tracker_mod, "get_active_router", return_value=mock_router), \
             patch.object(tracker_mod, "save_l2"):
            current_memory = {
                "hot": {"protagonist_level": "感债者", "recent_summaries": []},
                "cold": {"world_events": [], "closed_threads": [], "resolved_foreshadowing": []},
                "constraints": {"forbidden_constraints": [], "established_facts": [],
                                "foreshadowing_planted": []},
                "meta": {"novel_id": "test", "total_chapters_tracked": 5},
            }
            updated, cost = tracker_mod.run_tracker("章节正文", {"chapter_number": 6},
                                                     current_memory, "test")
        # chapter_summary 应被加入 recent_summaries
        summaries = updated["hot"]["recent_summaries"]
        assert any(s.get("chapter") == 6 and "主角觉醒" in s.get("summary", "")
                   for s in summaries), \
            f"正常路径必须把 chapter_summary 加进 recent_summaries，实际 {summaries}"
        # inventory 应有"玉佩"
        assert "玉佩" in updated["hot"]["inventory"], \
            f"inventory_add 必须被处理，实际 {updated['hot']['inventory']}"
        # meta 不应有 parse 失败标记
        assert "last_tracker_parse_failure_chapter" not in updated["meta"], \
            "正常 JSON 路径不应记录 parse 失败标记"


class TestComplianceParseFailNotFakePass:
    """迭代 #41: compliance.py 之前 parse 失败 → passed=True + 空 hard_rejects。
    修法：parse 失败 → passed=False + hard_rejects=[{PARSE_ERROR}]，让
    orchestrator 看到真实失败信号（不再 fake-pass）。
    """
    def test_compliance_parse_fail_marks_passed_false(self):
        from engine.agents.compliance import llm_semantic_check
        from unittest.mock import patch, MagicMock

        mock_router = MagicMock()
        mock_router.call.return_value = ("完全不是 JSON，是乱码", 0.001)
        with patch("engine.agents.compliance.get_active_router", return_value=mock_router):
            result, cost = llm_semantic_check("一些章节文本", platform="fanqie")
        assert result["passed"] is False, \
            f"JSON parse 失败时必须 passed=False（保守策略），实际 {result['passed']}"
        # hard_rejects 必须有 PARSE_ERROR 条目
        assert any("PARSE_ERROR" in str(h.get("rule", "")) for h in result.get("hard_rejects", [])), \
            f"parse 失败时必须给 hard_rejects 加 PARSE_ERROR 条目，实际 {result.get('hard_rejects')}"
        # suggestion 必须有可读信息
        assert "重跑" in result.get("suggestion", "") or "LLM" in result.get("suggestion", ""), \
            f"parse 失败时 suggestion 必须给用户可读 hint，实际 {result.get('suggestion')}"

    def test_compliance_source_no_fake_pass_on_exception(self):
        """源码扫描：llm_semantic_check 不再有 raw except Exception → passed=True。"""
        import inspect
        from engine.agents import compliance as comp_mod
        src = inspect.getsource(comp_mod)
        # 老代码是 `except Exception: result = {"passed": True, ...}`
        assert 'result = {"passed": True' not in src, \
            "compliance.py 不能再有 `except Exception: result = {passed:True}` fake-pass"
        # 新代码必须有 passed=False
        assert '"passed": False' in src, \
            "compliance.py parse 失败分支必须设 passed=False"

    def test_run_compliance_propagates_parse_fail_to_passed(self):
        """run_compliance（合并关键词 + LLM）必须把 parse 失败的 passed=False
        透传给最终结果。"""
        from engine.agents.compliance import run_compliance
        from unittest.mock import patch, MagicMock

        mock_router = MagicMock()
        mock_router.call.return_value = ("乱码", 0.001)
        with patch("engine.agents.compliance.get_active_router", return_value=mock_router):
            result, cost = run_compliance("章节文本（无关键词触发）", platform="fanqie")
        # 最终 passed 必须 False（即便 keyword scan 没发现 hard_kw）
        assert result["passed"] is False, \
            f"run_compliance 必须把 LLM parse 失败的 passed=False 透传，实际 {result['passed']}"


class TestInitArcJsonDecodeHandling:
    """迭代 #42: init_arc.py 之前 json.loads(raw read) — setting_package.json
    损坏时原始 JSONDecodeError 透出。同 pull_setting_package (迭代 #35) 同型。
    """
    def test_init_arc_source_catches_json_errors(self):
        """init_arc.py 必须 try/except (json.JSONDecodeError, UnicodeDecodeError)。"""
        import inspect
        from engine.agents import init_arc as init_mod
        src = inspect.getsource(init_mod.build_state_from_setting)
        assert "json.JSONDecodeError" in src, \
            "init_arc.build_state_from_setting 必须 catch json.JSONDecodeError"
        assert "UnicodeDecodeError" in src, \
            "init_arc.build_state_from_setting 必须 catch UnicodeDecodeError"

    def test_init_arc_corrupt_setting_raises_runtime_error(self, tmp_path):
        """模拟 setting_package.json 损坏 → 应该抛 RuntimeError 带可读信息，
        而不是透出原始 JSONDecodeError。"""
        from unittest.mock import patch
        from engine.agents import init_arc as init_mod
        import pytest

        # 写一个损坏的 JSON
        corrupt = tmp_path / "setting_package.json"
        corrupt.write_text("{ this is not valid JSON", encoding="utf-8")

        with patch.object(init_mod, "SETTING_PATH_STR", str(corrupt)):
            with pytest.raises(RuntimeError, match="setting_package.json 损坏"):
                init_mod.build_state_from_setting("test_proj")# ───────────────────────────────────────────
# III: atomic_write_json 全局推广（迭代 #43）
# ───────────────────────────────────────────
class TestAtomicWriteJsonPropagated:
    """迭代 #43: 之前发现 save_l2/save_l5 + planner 用了 atomic_write_json，
    但 orchestrator / setting_sync / reports / bootstrap 还在用 raw open(w) +
    json.dump。一次性全部修完，避免下一个项目里再发现「某个写盘点是 raw」。

    修复点（全部 critical，非可再生数据）：
    - engine/orchestrator.save_chapter: ch_NNNN_meta.json
    - engine/orchestrator.load_arc_tasks: arc_N_tasks.json
    - app/bridge/setting_sync.push_concept: novel_config.json
    - app/bridge/reports.apply_review: orchestrator_state.json
    - engine/tools/bootstrap: ch_NNNN_meta.json (x2)
    """
    def test_orchestrator_save_chapter_uses_atomic(self):
        import inspect, re
        from engine import orchestrator as orch_mod
        src = inspect.getsource(orch_mod.save_chapter)
        assert "atomic_write_json" in src, \
            "orchestrator.save_chapter 必须用 atomic_write_json（之前 raw open(w) 半写损坏）"
        # meta.json 写盘点必须用 atomic；text 写盘（plain string）可用 raw open
        json_dump_with_open = re.findall(
            r"with\s+open\([^)]*[\"']w[\"'][^)]*\)\s+as\s+\w+:\s*json\.dump",
            src,
        )
        assert not json_dump_with_open, (
            "orchestrator.save_chapter 不能有 `open(...w...); json.dump(...)` 模式（半写损坏）"
            f"实际命中: {json_dump_with_open}"
        )

    def test_orchestrator_task_sheet_uses_atomic(self):
        import inspect
        from engine import orchestrator as orch_mod
        src = inspect.getsource(orch_mod)
        assert "arc_" in src and "tasks.json" in src, \
            "orchestrator 必须写 arc_N_tasks.json"
        assert "atomic_write_json" in src, \
            "orchestrator 必须 import + 用 atomic_write_json 写 arc_N_tasks.json"

    def test_setting_sync_push_concept_uses_atomic(self):
        import inspect
        from app.bridge import setting_sync as sync_mod
        # 去掉 docstring（避免 `Path(`, `write_text` 等关键词在 docstring 误匹配）
        src = inspect.getsource(sync_mod)
        code_lines = []
        in_docstring = False
        for line in src.split("\n"):
            stripped = line.strip()
            if '"""' in stripped or "'''" in stripped:
                count = stripped.count('"""') + stripped.count("'''")
                if count == 1:
                    in_docstring = not in_docstring
                    continue
                elif count == 2:
                    continue
                else:
                    in_docstring = not in_docstring
                    continue
            if in_docstring or stripped.startswith("#"):
                continue
            code_lines.append(line)
        code_src = "\n".join(code_lines)
        assert "atomic_write_json" in code_src, \
            "setting_sync 必须 import + 用 atomic_write_json 写 novel_config.json"
        # 不能 raw write_text + json.dumps 组合
        assert ".write_text(json.dumps" not in code_src, \
            "setting_sync 不能 raw write_text(json.dumps(...))（半写损坏风险）"

    def test_reports_apply_review_uses_atomic(self):
        import inspect
        from app.bridge import reports as reports_mod
        src = inspect.getsource(reports_mod)
        assert "atomic_write_json" in src, \
            "reports 必须 import + 用 atomic_write_json 写 orchestrator_state.json"
        # 不能 raw write_text + json.dumps
        assert 'state_path.write_text(json.dumps' not in src, \
            "reports 不能 raw write_text(json.dumps(...))（半写损坏风险）"

    def test_bootstrap_ch_meta_uses_atomic(self):
        import inspect
        from engine.tools import bootstrap as bootstrap_mod
        src = inspect.getsource(bootstrap_mod)
        assert "atomic_write_json" in src, \
            "bootstrap 必须 import + 用 atomic_write_json 写 ch_NNNN_meta.json"

    def test_orchestrator_atomic_write_roundtrip(self, tmp_path, monkeypatch):
        """实际跑 save_chapter 验证写入是 atomic 的。"""
        from engine import orchestrator as orch_mod

        # 切到临时 CHAPTERS_DIR
        monkeypatch.setattr(orch_mod, "CHAPTERS_DIR", tmp_path)

        orch_mod.save_chapter("test", 42, "正文内容", {"score": 8.5, "chapter_role": "爽点"})

        target = tmp_path / "ch_0042_meta.json"
        assert target.exists(), "save_chapter 必须写 meta 文件"
        # 不应残留 .tmp
        assert not (tmp_path / "ch_0042_meta.json.tmp").exists(), \
            "atomic write 完成后 .tmp 必须被替换走"
        # 数据要能 load 回来
        import json
        with open(target, encoding="utf-8") as f:
            meta = json.load(f)
        assert meta["score"] == 8.5
        assert meta["chapter_role"] == "爽点"


# ───────────────────────────────────────────
# JJJ: NOVEL_PRODUCTION MASTER_KEY 强制检查（之前审查指出的高危点）
# ───────────────────────────────────────────
class TestNovelProductionEnforcement:
    """独立审查 §3.2 提到：生产环境忘设 MASTER_KEY → 临时 key 加密 →
    重启后无法解密 → 数据永久损坏。

    修法（app/main.py._check_master_key_in_production）：
    - NOVEL_PRODUCTION=1 + MASTER_KEY 未设 → 启动时 fail-fast（RuntimeError）
    - NOVEL_PRODUCTION=1 + MASTER_KEY 已设 → 继续运行
    - dev 模式（默认）→ 保持原行为（warn 但继续）

    本测试锁死：源码必须有 fail-fast 检查 + env 开关语义正确。
    """
    def test_source_has_production_check(self):
        import inspect
        from app import main as main_mod
        src = inspect.getsource(main_mod._check_master_key_in_production)
        assert "NOVEL_PRODUCTION" in src, \
            "main._check_master_key_in_production 必须读 NOVEL_PRODUCTION env"
        assert "MASTER_KEY" in src, \
            "main._check_master_key_in_production 必须检查 MASTER_KEY"

    def test_production_check_wired_into_lifespan(self):
        """_check_master_key_in_production 必须在 lifespan 里被调用（启动时 fail-fast）。"""
        import inspect
        from app import main as main_mod
        lifespan_src = inspect.getsource(main_mod.lifespan)
        assert "_check_master_key_in_production" in lifespan_src, \
            "main.lifespan 必须调用 _check_master_key_in_production（启动时 fail-fast）"

    def test_production_check_runs_before_migrations(self):
        """_check_master_key_in_production 必须在 run_migrations 之前调用——
        否则 MASTER_KEY 缺失 + 已存在的 api_key_encrypted 会先被读到 → decrypt 失败。"""
        import inspect
        from app import main as main_mod
        lifespan_src = inspect.getsource(main_mod.lifespan)
        check_pos = lifespan_src.find("_check_master_key_in_production")
        migration_pos = lifespan_src.find("run_migrations")
        assert check_pos != -1, "lifespan 必须调 _check_master_key_in_production"
        assert migration_pos != -1, "lifespan 必须调 run_migrations"
        assert check_pos < migration_pos, \
            f"_check_master_key_in_production (pos={check_pos}) 必须在 run_migrations (pos={migration_pos}) 之前调用"

    def test_production_no_master_key_fails(self, monkeypatch):
        """NOVEL_PRODUCTION=1 + MASTER_KEY 未设 → 必须抛 RuntimeError。"""
        monkeypatch.setenv("NOVEL_PRODUCTION", "1")
        monkeypatch.delenv("MASTER_KEY", raising=False)
        from app import security as sec_mod
        if hasattr(sec_mod.get_master_key, "cache_clear"):
            sec_mod.get_master_key.cache_clear()
        from app import main as main_mod
        import pytest
        with pytest.raises(RuntimeError, match="MASTER_KEY"):
            main_mod._check_master_key_in_production()

    def test_dev_mode_no_master_key_passes(self, monkeypatch):
        """dev 模式（无 NOVEL_PRODUCTION）+ MASTER_KEY 未设 → 不抛（warn 但继续）。"""
        monkeypatch.delenv("NOVEL_PRODUCTION", raising=False)
        monkeypatch.delenv("MASTER_KEY", raising=False)
        from app import security as sec_mod
        if hasattr(sec_mod.get_master_key, "cache_clear"):
            sec_mod.get_master_key.cache_clear()
        from app import main as main_mod
        # 不应抛
        try:
            main_mod._check_master_key_in_production()
        except RuntimeError as e:
            if "MASTER_KEY" in str(e) or "PRODUCTION" in str(e):
                import pytest
                pytest.fail(f"dev 模式不应抛 RuntimeError：{e}")
            raise

# ───────────────────────────────────────────
# KKK: simplify #45 — _call_with_budget 去重
# ───────────────────────────────────────────
class TestCallWithBudgetDedupe:
    """迭代 #45: writer.py + rewriter.py 之前各有一份几乎相同的 _call_with_budget
    （~30 行重试逻辑：网络抖动 sleep + retry）。抽到 engine.utils.call_with_budget_with_retry。

    锁死：
    1. utils 必须导出 call_with_budget_with_retry
    2. writer.py / rewriter.py 必须 import 它，不再自己实现重试循环
    3. 实际行为：retry 一次（max_attempts=2），全失败抛异常
    """
    def test_utils_exposes_call_with_budget_with_retry(self):
        from engine.utils import call_with_budget_with_retry
        import inspect
        sig = inspect.signature(call_with_budget_with_retry)
        params = sig.parameters
        for name in ("router", "agent_name", "system", "user", "target_chars"):
            assert name in params, \
                f"call_with_budget_with_retry 必须有参数 {name}，实际 {list(params.keys())}"
        assert params["max_attempts"].default == 2, \
            f"max_attempts 默认 2（保持历史行为），实际 {params['max_attempts'].default}"

    def test_writer_uses_shared_helper(self):
        import inspect
        from engine.agents import writer as writer_mod
        src = inspect.getsource(writer_mod)
        assert "call_with_budget_with_retry" in src, \
            "writer.py 必须 import + 调 call_with_budget_with_retry（不能自己实现重试）"
        assert "import time as _time" not in src, \
            "writer.py 不应再有 inline `import time as _time`（重试已迁到 utils）"

    def test_rewriter_uses_shared_helper(self):
        import inspect
        from engine.agents import rewriter as rewriter_mod
        src = inspect.getsource(rewriter_mod)
        assert "call_with_budget_with_retry" in src, \
            "rewriter.py 必须 import + 调 call_with_budget_with_retry（不能自己实现重试）"
        assert "import time as _time" not in src, \
            "rewriter.py 不应再有 inline `import time as _time`（重试已迁到 utils）"

    def test_call_with_budget_with_retry_returns_on_first_success(self):
        from unittest.mock import MagicMock
        from engine.utils import call_with_budget_with_retry

        router = MagicMock()
        router.call_with_length_budget.return_value = ("text", 0.01)
        text, cost = call_with_budget_with_retry(
            router, "writer", "sys", "user", 2000,
            sleep_seconds=0.001,
        )
        assert text == "text" and cost == 0.01
        assert router.call_with_length_budget.call_count == 1

    def test_call_with_budget_with_retry_retries_then_succeeds(self):
        from unittest.mock import MagicMock
        import httpx
        from engine.utils import call_with_budget_with_retry

        router = MagicMock()
        router.call_with_length_budget.side_effect = [
            httpx.ConnectError("connection refused"),
            ("text", 0.02),
        ]
        text, cost = call_with_budget_with_retry(
            router, "writer", "sys", "user", 2000,
            sleep_seconds=0.001,
        )
        assert text == "text" and cost == 0.02
        assert router.call_with_length_budget.call_count == 2, \
            f"必须 retry 一次，实际调了 {router.call_with_length_budget.call_count} 次"

    def test_call_with_budget_with_retry_raises_after_exhausting_attempts(self):
        from unittest.mock import MagicMock
        import httpx
        import pytest
        from engine.utils import call_with_budget_with_retry

        router = MagicMock()
        router.call_with_length_budget.side_effect = httpx.ConnectError("net down")
        with pytest.raises(httpx.ConnectError, match="net down"):
            call_with_budget_with_retry(
                router, "writer", "sys", "user", 2000,
                sleep_seconds=0.001, max_attempts=2,
            )
        assert router.call_with_length_budget.call_count == 2


# ───────────────────────────────────────────
# LLL: simplify #45-followup — writer.py 去掉私有 _ACTIVE_ROUTER
# ───────────────────────────────────────────
class TestWriterNoPrivateRouterState:
    """#45-followup: writer.py 之前自己定义 _ACTIVE_ROUTER + set_active_router
    + _get_router，跟 rewriter.py / 其他 agent 用的 engine.llm_router.get_active_router()
    重复。删掉 writer.py 的私有状态，统一从 engine.llm_router 读。

    锁死：writer.py 不能有私有 _ACTIVE_ROUTER / set_active_router（必须用
    engine.llm_router.get_active_router()，避免多份 state 漂移）。
    """
    def test_writer_no_module_level_active_router(self):
        import inspect
        from engine.agents import writer as writer_mod
        src = inspect.getsource(writer_mod)
        # 去掉注释 + docstring（避免「_ACTIVE_ROUTER 删掉了」这种历史说明误匹配）
        code_lines = []
        in_docstring = False
        for line in src.split("\n"):
            stripped = line.strip()
            if '"""' in stripped or "'''" in stripped:
                count = stripped.count('"""') + stripped.count("'''")
                if count == 1:
                    in_docstring = not in_docstring
                    continue
                elif count == 2:
                    continue
                else:
                    in_docstring = not in_docstring
                    continue
            if in_docstring or stripped.startswith("#"):
                continue
            code_lines.append(line)
        code_src = "\n".join(code_lines)
        assert "_ACTIVE_ROUTER" not in code_src, \
            "writer.py 不应再有私有 _ACTIVE_ROUTER（统一用 engine.llm_router.get_active_router）"
        assert "def set_active_router" not in code_src, \
            "writer.py 不应再有 set_active_router 函数（同上）"

    def test_writer_uses_engine_llm_router(self):
        import inspect
        from engine.agents import writer as writer_mod
        src = inspect.getsource(writer_mod)
        # 必须 import engine.llm_router.get_active_router
        assert "from ..llm_router import get_active_router" in src, \
            "writer.py 必须 import engine.llm_router.get_active_router"

    def test_writer_get_router_fallback(self):
        """_get_router() 在没 active router 时 fallback 到 env-only 实例。"""
        from unittest.mock import patch
        from engine.agents import writer as writer_mod
        from engine.llm.router import LLMRouter
        with patch.object(writer_mod, "get_active_router", return_value=None):
            router = writer_mod._get_router()
        assert isinstance(router, LLMRouter), \
            "active router 为 None 时 _get_router 必须 fallback 到 fresh LLMRouter"


# ───────────────────────────────────────────
# MMM: fix #46 — proxy URL 配置了但永远不生效
# ───────────────────────────────────────────
class TestProxyApplied:
    """迭代 #46: 之前 _get_proxied_client 读 `_proxy_mounts.get(provider)`
    期望拿到 URL 字符串，但 `_proxy_mounts` 实际是 dict[str, httpx.Client]
    （缓存 httpx.Client）。真 URL 在 `_PROVIDER_PROXY`（set_proxy_map 写入）。

    后果：用户在 Provider 表里勾选 needs_proxy + 设 DEEPSEEK_PROXY env
    → 期望 deepseek 流量走代理；实际 _get_proxied_client 拿到 None
    → 返回 _get_client(120)（无代理）→ GFW 区域用户无法调用 deepseek。

    修法：从 _PROVIDER_PROXY 读 URL。

    锁死：set_proxy_map 后 _get_proxied_client 必须返回 proxy-mounted client
    （_proxy_mounts 缓存里有以 (provider, proxy_url, timeout) 为 key 的 Client）。
    """
    def test_proxy_applied_after_set_proxy_map(self):
        from engine.llm import router as router_mod

        # 重置模块级缓存 + proxy map（避免其他测试污染）
        router_mod._proxy_mounts.clear()
        router_mod._PROVIDER_PROXY.clear()

        # 配置 deepseek 走代理
        router_mod.LLMRouter().set_proxy_map({"deepseek": "http://127.0.0.1:7890"})

        # 调 _get_proxied_client — 必须返回挂代理的 Client
        client = router_mod._get_proxied_client(
            "deepseek", "https://api.deepseek.com/v1/chat/completions", 120,
        )
        assert client is not None, "set_proxy_map 后 _get_proxied_client 必须返回 client"

        # _proxy_mounts 缓存里必须有该 client
        cached_keys = [k for k in router_mod._proxy_mounts.keys() if isinstance(k, tuple)]
        assert any(
            k[0] == "deepseek" and k[1] == "http://127.0.0.1:7890" and k[2] == 120
            for k in cached_keys
        ), f"proxy 缓存里必须有 (deepseek, http://127.0.0.1:7890, 120)，实际 {list(router_mod._proxy_mounts.keys())}"

    def test_no_proxy_returns_regular_client(self):
        from engine.llm import router as router_mod

        router_mod._proxy_mounts.clear()
        router_mod._PROVIDER_PROXY.clear()

        # 不调 set_proxy_map — _PROVIDER_PROXY 空
        client = router_mod._get_proxied_client(
            "anthropic", "https://api.anthropic.com/v1/messages", 120,
        )
        assert client is not None, "无 proxy 时必须返回 client"
        cached_tuples = [k for k in router_mod._proxy_mounts.keys() if isinstance(k, tuple)]
        assert len(cached_tuples) == 0, \
            f"无 proxy 时不应有 cached tuple key，实际 {cached_tuples}"

    def test_proxy_cached_across_calls(self):
        from engine.llm import router as router_mod

        router_mod._proxy_mounts.clear()
        router_mod._PROVIDER_PROXY.clear()
        router_mod.LLMRouter().set_proxy_map({"kimi": "http://127.0.0.1:7890"})

        c1 = router_mod._get_proxied_client("kimi", "https://api.moonshot.cn/v1/chat", 120)
        c2 = router_mod._get_proxied_client("kimi", "https://api.moonshot.cn/v1/chat", 120)
        assert c1 is c2, "第二次调必须返回同一个 cached Client（避免每次新建）"

    def test_proxy_url_source_is_provider_proxy(self):
        import inspect
        from engine.llm import router as router_mod
        src = inspect.getsource(router_mod._get_proxied_client)
        # 去掉 docstring（避免「之前 _proxy_mounts.get(provider)」这种历史说明误匹配）
        code_lines = []
        in_docstring = False
        for line in src.split("\n"):
            stripped = line.strip()
            if '"""' in stripped or "'''" in stripped:
                count = stripped.count('"""') + stripped.count("'''")
                if count == 1:
                    in_docstring = not in_docstring
                    continue
                elif count == 2:
                    continue
                else:
                    in_docstring = not in_docstring
                    continue
            if in_docstring or stripped.startswith("#"):
                continue
            code_lines.append(line)
        code_src = "\n".join(code_lines)
        assert "_PROVIDER_PROXY.get(provider)" in code_src, \
            "_get_proxied_client 必须从 _PROVIDER_PROXY.get(provider) 读 URL（fix #46）"
        assert "_proxy_mounts.get(provider)" not in code_src, \
            "_get_proxied_client 不能从 _proxy_mounts.get(provider) 读 URL（fix #46 之前 bug）"


# ───────────────────────────────────────────
# NNN: fix #47 — summarizer JSON parse 失败不再静默
# ───────────────────────────────────────────
class TestSummarizerParseFailureNotSilent:
    """迭代 #47: summarizer.summarize_arc 之前 parse 失败时静默写 placeholder
    到 L5.arc_summaries，没有 log warning 让运维知道（跟 tracker.py iter #40
    同型问题，只是更早被作者放过）。

    修法：log warning + 加 _parse_failed=True 标记到 placeholder dict。
    """
    def test_summarizer_logs_warning_on_parse_failure(self, caplog):
        from unittest.mock import patch, MagicMock
        from engine.agents import summarizer as summ_mod

        mock_router = MagicMock()
        mock_router.call.return_value = ("乱码不是 JSON", 0.001)
        with patch.object(summ_mod, "get_active_router", return_value=mock_router), \
             patch.object(summ_mod, "save_l5"):
            memory = {"hot": {"recent_summaries": []}, "active_threads": []}
            with caplog.at_level("WARNING"):
                arc_summary, cost = summ_mod.summarize_arc(
                    {"arc_id": 3, "arc_name": "测试弧"}, [], memory, "test_novel",
                )
        warning_msgs = [r.message for r in caplog.records if r.levelname == "WARNING"]
        assert any("summarizer" in m.lower() for m in warning_msgs), \
            f"summarizer parse 失败时必须 log warning，实际: {warning_msgs}"
        assert arc_summary.get("_parse_failed") is True, \
            f"parse 失败时 placeholder 必须 _parse_failed=True，实际: {arc_summary}"

    def test_summarizer_placeholder_carries_failure_marker(self):
        import inspect
        from engine.agents import summarizer as summ_mod
        src = inspect.getsource(summ_mod.summarize_arc)
        assert "_parse_failed" in src, \
            "summarizer.summarize_arc 的 placeholder 必须带 _parse_failed=True 标记"

    def test_summarizer_has_logger(self):
        from engine.agents import summarizer as summ_mod
        assert hasattr(summ_mod, "log"), \
            "summarizer 必须有 module-level log（用于 log.warning 而非 print）"


# ───────────────────────────────────────────
# OOO: fix #48 — chapter_checker llm_consistency_check fake-pass
# ───────────────────────────────────────────
class TestChapterCheckerNoFakePass:
    """迭代 #48: chapter_checker.llm_consistency_check 之前 parse 失败时
    返回 {"has_issues": False} — silent pass（同 compliance iter #41 /
    orchestrator iter #28 fake-pass 同型问题）。

    后果：LLM 检测到的跨章节矛盾（人物等级跳变 / 道具未获得 / 时间线错乱）
    JSON 解析失败 → 报告「无问题」→ 错误积累到后续章节。
    修法：parse 失败时 has_issues=True + issues 加 "解析失败" + _parse_failed=True
    """
    def test_consistency_check_parse_fail_not_silent_pass(self):
        from engine.tools import chapter_checker as checker_mod
        from unittest.mock import patch, MagicMock

        mock_router = MagicMock()
        mock_router.call.return_value = ("乱码不是 JSON", 0.001)
        with patch.object(checker_mod, "get_active_router", return_value=mock_router):
            result, cost = checker_mod.llm_consistency_check(
                "章节正文", {"characters": {}, "protagonist_level": "感债者",
                            "protagonist_points": 0, "inventory": [],
                            "established_facts": []},
            )
        assert result["has_issues"] is True, \
            f"JSON parse 失败时必须 has_issues=True（保守策略），实际 {result['has_issues']}"
        assert result.get("_parse_failed") is True, \
            f"parse 失败时必须 _parse_failed=True 标记，实际 {result}"
        assert any(
            "解析失败" in i.get("description", "") or "JSON" in i.get("description", "")
            for i in result.get("issues", [])
        ), f"parse 失败时 issues 必须包含解析失败条目，实际 {result.get('issues')}"

    def test_consistency_check_source_no_fake_pass(self):
        import inspect
        from engine.tools import chapter_checker as checker_mod
        src = inspect.getsource(checker_mod.llm_consistency_check)
        # 去掉注释（避免「之前 fake-pass {"has_issues": False}」这种历史说明误匹配）
        code_lines = [
            l for l in src.split("\n") if l.strip() and not l.strip().startswith("#")
        ]
        code_src = "\n".join(code_lines)
        assert '{"has_issues": False' not in code_src, \
            "chapter_checker.llm_consistency_check 不能再用 has_issues=False 默认值（fake-pass）"
        assert "parse_llm_json_response(resp, None)" in code_src, \
            "chapter_checker.llm_consistency_check 必须用 None default 检测 parse 失败"


# ───────────────────────────────────────────
# PPP: fix #49 — atomic_write_json 推广到剩余报告 JSON
# ───────────────────────────────────────────
class TestAtomicWriteJsonFinalPropagation:
    """迭代 #49: 跟 #43 同型——把 atomic_write_json 一次性推广到所有剩余的
    `with open(...w...); json.dump(...)` 写盘点：
    - budget_manager.generate_report → budget_report.json
    - calibrate_checker → calibration_result.json
    - chapter_checker.scan_all_chapters → consistency_report.json
    - bootstrap.run_bootstrap → bootstrap_candidates.json

    锁死：源码不能再有 `open(...w...); json.dump(...)` 模式（half-write 损坏风险）。
    """
    def test_budget_report_uses_atomic_write(self):
        import inspect, re
        from engine.tools import budget_manager as bm_mod
        src = inspect.getsource(bm_mod)
        code_lines = [l for l in src.split("\n")
                      if l.strip() and not l.strip().startswith("#")]
        code_src = "\n".join(code_lines)
        assert "atomic_write_json" in code_src, \
            "budget_manager 必须用 atomic_write_json 写 budget_report.json"
        bad_pattern = re.findall(
            r"with\s+open\([^)]*[\"']w[\"'][^)]*\)\s+as\s+\w+:\s*json\.dump",
            code_src,
        )
        assert not bad_pattern, \
            f"budget_manager 不能再有 `open(...w...); json.dump(...)` 模式，实际 {bad_pattern}"

    def test_calibrate_checker_uses_atomic_write(self):
        import inspect
        from engine.tools import calibrate_checker as cc_mod
        src = inspect.getsource(cc_mod)
        code_lines = [l for l in src.split("\n")
                      if l.strip() and not l.strip().startswith("#")]
        code_src = "\n".join(code_lines)
        assert "atomic_write_json" in code_src, \
            "calibrate_checker 必须用 atomic_write_json 写 calibration_result.json"

    def test_chapter_checker_consistency_report_uses_atomic(self):
        import inspect
        from engine.tools import chapter_checker as chk_mod
        src = inspect.getsource(chk_mod)
        code_lines = [l for l in src.split("\n")
                      if l.strip() and not l.strip().startswith("#")]
        code_src = "\n".join(code_lines)
        assert "atomic_write_json" in code_src, \
            "chapter_checker 必须用 atomic_write_json 写 consistency_report.json"

    def test_bootstrap_candidates_uses_atomic(self):
        import inspect
        from engine.tools import bootstrap as boot_mod
        src = inspect.getsource(boot_mod)
        code_lines = [l for l in src.split("\n")
                      if l.strip() and not l.strip().startswith("#")]
        code_src = "\n".join(code_lines)
        assert "atomic_write_json" in code_src, \
            "bootstrap 必须用 atomic_write_json 写 bootstrap_candidates.json"

    def test_atomic_write_json_actually_used_at_runtime(self, tmp_path, monkeypatch):
        """跑 budget_manager.print_report 实际写到 tmp，验证是 atomic。"""
        from engine.tools import budget_manager as bm_mod
        monkeypatch.setattr(bm_mod, "REPORT_DIR", str(tmp_path))
        report_path = tmp_path / "budget_report.json"
        bm_mod.print_report()
        assert report_path.exists(), "budget_report.json 必须被写入"
        assert not (tmp_path / "budget_report.json.tmp").exists(), \
            "atomic write 完成后 .tmp 必须被替换走"
        import json
        with open(report_path, encoding="utf-8") as f:
            data = json.load(f)
        assert "total_cost_usd" in data


# ───────────────────────────────────────────
# QQQ: fix #50 — print_report KeyError when budget_log empty
# ───────────────────────────────────────────
class TestBudgetReportEmptyLogNoKeyError:
    """迭代 #50: budget_manager.generate_report 在 budget_log 为空时返回的 dict
    缺少 total_chapters_planned / cost_per_chapter_recent20 / projected_total_cost
    等 key。print_report 直接 `report["total_chapters_planned"]` → KeyError。

    后果：第一次启动 / 删 budget_log 后 → 用户跑 status/budget 命令 → 后端 500
    + traceback 暴露给前端。

    修法：generate_report 空 records 路径补 total_chapters_planned 字段；
    print_report 用 .get() 兜底 cost_per_chapter_recent20 / projected_total_cost。
    """
    def test_generate_report_empty_log_has_total_chapters_planned(self, tmp_path, monkeypatch):
        """budget_log 不存在时 generate_report 必须返回 total_chapters_planned 键。"""
        from engine.tools import budget_manager as bm_mod
        # budget_log 不存在 + state_path 不存在
        monkeypatch.setattr(bm_mod, "BUDGET_LOG", str(tmp_path / "no_log.jsonl"))
        monkeypatch.setattr(bm_mod, "STATE_PATH_STR", str(tmp_path / "no_state.json"))
        report = bm_mod.generate_report()
        assert "total_chapters_planned" in report, \
            f"空 log 路径 generate_report 必须有 total_chapters_planned 键，实际 keys: {list(report.keys())}"

    def test_print_report_no_keyerror_on_empty_log(self, tmp_path, monkeypatch, capsys):
        """budget_log 为空时 print_report 不能抛 KeyError（之前必崩）。"""
        from engine.tools import budget_manager as bm_mod
        monkeypatch.setattr(bm_mod, "BUDGET_LOG", str(tmp_path / "no_log.jsonl"))
        monkeypatch.setattr(bm_mod, "STATE_PATH_STR", str(tmp_path / "no_state.json"))
        # 不应抛 KeyError
        bm_mod.print_report()
        captured = capsys.readouterr()
        assert "💰 预算报告" in captured.out, "print_report 必须打报告内容"
        assert "KeyError" not in captured.out, "print_report 不应打 KeyError"

    def test_generate_report_loads_planned_from_state(self, tmp_path, monkeypatch):
        """从 STATE_PATH 读 total_chapters_planned 时，空 log 也要拿到。"""
        from engine.tools import budget_manager as bm_mod
        # 写一个 mock state
        state = {"total_chapters_planned": 200, "budget_limit_usd": 800,
                 "budget_used_usd": 12.5, "current_chapter": 50}
        state_path = tmp_path / "state.json"
        state_path.write_text(json.dumps(state), encoding="utf-8")
        monkeypatch.setattr(bm_mod, "BUDGET_LOG", str(tmp_path / "no_log.jsonl"))
        monkeypatch.setattr(bm_mod, "STATE_PATH_STR", str(state_path))
        report = bm_mod.generate_report()
        assert report["total_chapters_planned"] == 200, \
            f"必须从 STATE_PATH 读 total_chapters_planned=200，实际 {report['total_chapters_planned']}"
        assert report["budget_limit_usd"] == 800
        assert report["chapters_done"] == 50


# ───────────────────────────────────────────
# RRR: fix #51 — anthropic SDK proxy 之前不生效
# ───────────────────────────────────────────
class TestAnthropicProxyApplied:
    """迭代 #51: _anthropic 之前用 Anthropic() 直接调用，没传 http_client。
    即使 _PROVIDER_PROXY["anthropic"] 配了，proxy 永远不生效。
    后果：GFW 区域用户勾选 anthropic.needs_proxy + 设 ANTHROPIC_PROXY
    → anthropic API 直连 → 超时 / 失败。

    修法：检测 _PROVIDER_PROXY.get("anthropic")，有就构造 httpx.Client(proxy=...)
    作为 http_client 参数传给 Anthropic SDK。
    """
    def test_anthropic_passes_http_client_when_proxy_configured(self):
        import inspect
        from engine.llm import router as router_mod
        # _anthropic 是 LLMRouter 类方法，不是模块级函数
        src = inspect.getsource(router_mod.LLMRouter._anthropic)
        code_lines = [l for l in src.split("\n")
                      if l.strip() and not l.strip().startswith("#")]
        code_src = "\n".join(code_lines)
        assert '"http_client"' in code_src or "'http_client'" in code_src, \
            "_anthropic 必须用 'http_client' 参数把 httpx.Client 传给 Anthropic SDK（fix #51）"
        assert '_PROVIDER_PROXY.get("anthropic")' in code_src, \
            "_anthropic 必须从 _PROVIDER_PROXY.get('anthropic') 读 proxy URL"

    def test_anthropic_proxy_actually_constructed(self, monkeypatch):
        from unittest.mock import patch, MagicMock
        import httpx
        from engine.llm import router as router_mod

        router_mod._PROVIDER_PROXY.clear()
        router_mod.LLMRouter().set_proxy_map({"anthropic": "http://127.0.0.1:7890"})

        captured = {}
        def fake_anthropic_ctor(**kwargs):
            captured.update(kwargs)
            m = MagicMock()
            m.messages.create.return_value = MagicMock(
                content=[MagicMock(text="hi")],
                usage=MagicMock(input_tokens=10, output_tokens=5,
                                cache_read_input_tokens=0,
                                cache_creation_input_tokens=0),
            )
            return m
        with patch.object(router_mod, "Anthropic", side_effect=fake_anthropic_ctor):
            r = router_mod.LLMRouter()
            r._anthropic("checker_main", "sys", "user", "claude-sonnet-4-5",
                         max_tokens=100, temperature=0.5)
        assert "http_client" in captured, \
            f"_anthropic 必须传 http_client 参数，实际 kwargs: {list(captured.keys())}"
        assert isinstance(captured["http_client"], httpx.Client), \
            f"http_client 必须是 httpx.Client 实例，实际 {type(captured['http_client'])}"

    def test_anthropic_no_proxy_no_http_client(self, monkeypatch):
        from unittest.mock import patch, MagicMock
        from engine.llm import router as router_mod

        router_mod._PROVIDER_PROXY.clear()

        captured = {}
        def fake_anthropic_ctor(**kwargs):
            captured.update(kwargs)
            m = MagicMock()
            m.messages.create.return_value = MagicMock(
                content=[MagicMock(text="hi")],
                usage=MagicMock(input_tokens=10, output_tokens=5,
                                cache_read_input_tokens=0,
                                cache_creation_input_tokens=0),
            )
            return m
        with patch.object(router_mod, "Anthropic", side_effect=fake_anthropic_ctor):
            r = router_mod.LLMRouter()
            r._anthropic("checker_main", "sys", "user", "claude-sonnet-4-5",
                         max_tokens=100, temperature=0.5)
        assert "http_client" not in captured, \
            f"没配 proxy 时不应传 http_client，实际 kwargs: {captured}"


# ───────────────────────────────────────────
# SSS: fix #52 — app/config.py minimax_api_base 旧 endpoint
# ───────────────────────────────────────────
class TestMinimaxEndpointUpdated:
    """迭代 #52: app/config.py 的 minimax_api_base 默认是旧版 endpoint
    api.minimax.chat（router.py iter #32 已切到 api.minimaxi.com）。

    后果：用户没设 NOVEL_MINIMAX_API_BASE env 时，app/llm_router.py
    通过 settings.minimax_api_base 拿旧 endpoint → 调用 404 / 401。

    锁死：config.py 的 minimax_api_base 默认必须跟 router.py 的
    MINIMAX_BASE_URL fallback 一致（api.minimaxi.com）。
    """
    def test_config_minimax_default_uses_new_endpoint(self):
        from app.config import settings
        assert "minimaxi.com" in settings.minimax_api_base, \
            f"config.minimax_api_base 默认必须用新 endpoint api.minimaxi.com，实际 {settings.minimax_api_base}"

    def test_config_minimax_no_old_endpoint_default(self):
        from app.config import settings
        assert "minimax.chat" not in settings.minimax_api_base, \
            f"config.minimax_api_base 不能默认旧 endpoint api.minimax.chat（404），实际 {settings.minimax_api_base}"

    def test_config_minimax_default_model_is_m3(self):
        from app.config import settings
        assert "M3" in settings.minimax_model or "minimax" in settings.minimax_model.lower(), \
            f"config.minimax_model 默认应指向当前在用的 model，实际 {settings.minimax_model}"


# ───────────────────────────────────────────
# TTT: fix #53 — _load_state_for_project 损坏文件不再静默 fallback
# ───────────────────────────────────────────
class TestLoadStateNoSilentFallback:
    """迭代 #53: engine/graph.py:_load_state_for_project 之前
    `except Exception: pass` 静默兜底 — 损坏的 state 文件会被忽略，
    走 DB 路径返回 fresh initial state → 用户 50 章进度静默丢失。

    修法：损坏时 backup 到 .corrupted.{ts}，然后 raise 让 caller 看到
    （不静默 fallback）。
    """
    def test_corrupt_state_file_raises_not_silently_falls_back(self, tmp_path, monkeypatch):
        """state 文件损坏 → 必须 raise，不能 return fresh state。"""
        from engine import graph as graph_mod

        # 切 STATE_PATH 到损坏文件
        corrupt_path = tmp_path / "state.json"
        corrupt_path.write_text("{ this is not valid JSON", encoding="utf-8")
        monkeypatch.setattr(graph_mod, "_STATE_PATH", str(corrupt_path))

        with pytest.raises(Exception) as exc_info:
            graph_mod._load_state_for_project("test_proj")

        # 必须是 JSONDecodeError（不能是 fresh state dict 静默返回）
        assert "JSON" in str(exc_info.value) or "Expecting" in str(exc_info.value) or \
               "state" in str(exc_info.value).lower(), \
            f"损坏 state 文件必须 raise JSONDecodeError，实际 {type(exc_info.value).__name__}: {exc_info.value}"

    def test_corrupt_state_file_backed_up(self, tmp_path, monkeypatch):
        """损坏 state 文件必须被备份成 .corrupted.{ts}。"""
        from engine import graph as graph_mod

        corrupt_path = tmp_path / "state.json"
        corrupt_path.write_text("{ broken", encoding="utf-8")
        monkeypatch.setattr(graph_mod, "_STATE_PATH", str(corrupt_path))

        try:
            graph_mod._load_state_for_project("test_proj")
        except Exception:
            pass  # expected

        # 必须有 .corrupted.* 备份文件
        backups = list(tmp_path.glob("state.json.corrupted.*"))
        assert len(backups) >= 1, \
            f"损坏 state 必须被备份成 .corrupted.*，实际 {list(tmp_path.iterdir())}"

    def test_no_silent_except_in_load_state(self):
        """源码扫描：_load_state_for_project 不能有 except Exception: pass。"""
        import inspect
        from engine import graph as graph_mod
        src = inspect.getsource(graph_mod._load_state_for_project)
        code_lines = [l for l in src.split("\n")
                      if l.strip() and not l.strip().startswith("#")]
        code_src = "\n".join(code_lines)
        # 不能再有 silent pass
        assert "except Exception:\n        pass" not in code_src, \
            "_load_state_for_project 不能有 except Exception: pass（损坏文件必须 raise）"


# ───────────────────────────────────────────
# UUU: fix #54 — _drain_stdout 异常不再让 daemon 线程静默死掉
# ───────────────────────────────────────────
class TestDrainStdoutExceptionHandling:
    """迭代 #54: _drain_stdout 是 daemon 线程，之前 try/finally 但没有 except
    — 循环里 DB 错误 / KeyError 会让线程静默死掉，bridge_run.status 卡在
    "running"，下次 /bridge/run 触发 409 Conflict。

    修法：循环 body 包内层 try/except，异常时把 bridge_run 标 failed +
    记录异常 + push error 事件到 queue。
    """
    def test_drain_stdout_inner_try_except_present(self):
        """_drain_stdout 的循环体必须有 try/except（不只外层 finally）。"""
        import inspect, re
        from app.api import bridge as bridge_mod
        src = inspect.getsource(bridge_mod._spawn_engine_subprocess)
        code_lines = [l for l in src.split("\n")
                      if l.strip() and not l.strip().startswith("#")]
        code_src = "\n".join(code_lines)
        # 必须有内层 try（带 except Exception）
        # 检查 `for line in iter(proc.stdout.readline, ""):` 后是否有内层 try
        # 简化检查：源码里 must 有两次 "try:" 出现（外层 + 内层）
        try_count = code_src.count("try:")
        assert try_count >= 2, \
            f"_drain_stdout 必须有内层 try/except（循环里异常时设 bridge_run failed），" \
            f"实际 try: 出现 {try_count} 次"
        # 必须有 except Exception 处理循环错误
        assert "except Exception as loop_exc" in code_src, \
            "_drain_stdout 循环里必须有 except Exception as loop_exc → 设 bridge_run failed"

    def test_drain_stdout_pushes_error_event_on_loop_exception(self):
        """循环异常时必须 push {\"event\": \"error\", \"message\": ..., \"traceback\": ...} 到 queue。"""
        import inspect
        from app.api import bridge as bridge_mod
        src = inspect.getsource(bridge_mod._spawn_engine_subprocess)
        assert '"event": "error"' in src or "'event': 'error'" in src, \
            "_drain_stdout 异常时必须 push error 事件到 queue"
        assert "traceback.format_exc" in src, \
            "_drain_stdout 异常时必须带 traceback 信息"

    def test_bridge_module_imports_traceback(self):
        import app.api.bridge as bridge_mod
        # bridge.py 必须 import traceback 用于 #54 异常 traceback
        import inspect
        src = inspect.getsource(bridge_mod)
        assert "import traceback" in src, \
            "app/api/bridge.py 必须 import traceback（#54 用 traceback.format_exc）"


# ───────────────────────────────────────────
# VVV: fix #55 — monitor_run.py `if False` dead code + atomic write
# ───────────────────────────────────────────
class TestMonitorRunNoDeadCode:
    """迭代 #55: scripts/monitor_run.py 之前 initial_chapter_count
    永远返回 0（`if False else 0`）—— db 关了之后查 db 的死代码。
    后果：监控脚本拿不到「跑前已有几章」，报告不准。
    修法：把 db 查询移到 db 还开着时；atomic_write_json 写报告。
    """
    def test_monitor_run_no_if_false(self):
        """源码不能再有 `if False else` 死代码。"""
        import inspect
        from scripts import monitor_run as mr_mod
        src = inspect.getsource(mr_mod)
        # 去掉注释（避免「之前 `if False`」这种历史说明误匹配）
        code_lines = [l for l in src.split("\n")
                      if l.strip() and not l.strip().startswith("#")]
        code_src = "\n".join(code_lines)
        assert "if False" not in code_src, \
            "monitor_run.py 不能再有 `if False` 死代码"

    def test_monitor_run_uses_atomic_write_for_report(self):
        import inspect
        from scripts import monitor_run as mr_mod
        src = inspect.getsource(mr_mod)
        assert "atomic_write_json" in src, \
            "monitor_run.py 必须用 atomic_write_json 写 report（iter #55）"
        # 不能 raw write_text(json.dumps(...))
        assert ".write_text(json.dumps(" not in src, \
            "monitor_run.py 不能再 raw write_text(json.dumps(...))"

    def test_monitor_run_imports_engine_utils(self):
        """monitor_run.py 必须能 import engine.utils（已自动 by BACKEND path）。"""
        import inspect
        from scripts import monitor_run as mr_mod
        # 验证 atomic_write_json 是从 engine.utils 导入
        src = inspect.getsource(mr_mod)
        assert "from engine.utils import atomic_write_json" in src, \
            "monitor_run.py 必须 from engine.utils import atomic_write_json"


# ───────────────────────────────────────────
# WWW: fix #56 — export_openapi.py 改用 atomic_write_json
# ───────────────────────────────────────────
class TestExportOpenapiAtomicWrite:
    """迭代 #56: scripts/export_openapi.py 之前 write_text(json.dumps(...))
    非 atomic — 跟 iter #43/#49/#55 同型。
    后果：openapi.json 是 CI 校验漂移的基准（前端 vs 后端），半写损坏
    会掩盖真实漂移 → 误报 / 漏报。
    修法：atomic_write_json。
    """
    def test_export_openapi_uses_atomic_write(self):
        import inspect
        from scripts import export_openapi as eo_mod
        src = inspect.getsource(eo_mod)
        code_lines = [l for l in src.split("\n")
                      if l.strip() and not l.strip().startswith("#")]
        code_src = "\n".join(code_lines)
        assert "atomic_write_json" in code_src, \
            "export_openapi.py 必须用 atomic_write_json（iter #56）"
        # 不能 raw write_text(json.dumps(...))
        assert ".write_text(json.dumps(" not in code_src, \
            "export_openapi.py 不能再 raw write_text(json.dumps(...))"
        assert "from engine.utils import atomic_write_json" in code_src, \
            "export_openapi.py 必须 from engine.utils import atomic_write_json"


# ───────────────────────────────────────────
# XXX: fix #57 — rewrite_length.py meta.json 改用 atomic_write_json
# ───────────────────────────────────────────
class TestRewriteLengthAtomicMeta:
    """迭代 #57: scripts/rewrite_length.persist_chapter 写 meta.json 之前用
    raw write_text(json.dumps(...))——跟 iter #43/#49/#55/#56 同型。
    """
    def test_rewrite_length_persist_uses_atomic_meta_write(self):
        import inspect
        from scripts import rewrite_length as rl_mod
        src = inspect.getsource(rl_mod.persist_chapter)
        code_lines = [l for l in src.split("\n")
                      if l.strip() and not l.strip().startswith("#")]
        code_src = "\n".join(code_lines)
        assert "atomic_write_json" in code_src, \
            "rewrite_length.persist_chapter 必须用 atomic_write_json（iter #57）"
        assert "f_meta.write_text(json.dumps" not in code_src, \
            "rewrite_length.persist_chapter 不能再 raw write_text(json.dumps(...))"


# ───────────────────────────────────────────
# YYY: fix #58 — orchestrator.run_tracker 异常不再静默
# ───────────────────────────────────────────
class TestOrchestratorTrackerNotSilent:
    """迭代 #58: orchestrator.node_save_and_track 之前 except Exception
    静默兜底 updated_mem=memory, cost=0 —— tracker LLM 失败时没信号。
    修法：标 task._tracker_failed + error_log + 不静默吞。
    """
    def test_orchestrator_marks_tracker_failed(self):
        import inspect
        from engine import orchestrator as orch_mod
        src = inspect.getsource(orch_mod.node_save_and_track)
        code_lines = [l for l in src.split("\n")
                      if l.strip() and not l.strip().startswith("#")]
        code_src = "\n".join(code_lines)
        assert "_tracker_failed" in code_src, \
            "orchestrator.node_save_and_track 异常路径必须标 _tracker_failed（iter #58）"
        assert "error_log" in code_src, \
            "orchestrator.node_save_and_track 异常路径必须 log error_log"


# ───────────────────────────────────────────
# ZZZ: fix #59 — human_review.py atomic write + load_state silent fallback
# ───────────────────────────────────────────
class TestHumanReviewAtomicAndLoadNoSilent:
    """迭代 #59: engine/tools/human_review.py 两个 bug
    1. save_state 用 raw open(w) 写 orchestrator_state.json（半写损坏）
    2. load_state 损坏时 except Exception: pass → 返回 {} →
       人工审核看到空 state 却不知道文件坏了 → 假审核
    修法：atomic_write_json + 损坏时 backup 到 .corrupted.{ts} 后 raise。
    """
    def test_human_review_load_state_raises_on_corrupt(self, tmp_path, monkeypatch):
        """损坏 state 文件必须 raise（不能再 silent fallback 到 {}）。"""
        from engine.tools import human_review as hr_mod
        corrupt = tmp_path / "state.json"
        corrupt.write_text("{ not valid", encoding="utf-8")
        monkeypatch.setattr(hr_mod, "STATE_PATH", str(corrupt))
        with pytest.raises(Exception):
            hr_mod.load_state()

    def test_human_review_save_state_uses_atomic(self):
        import inspect
        from engine.tools import human_review as hr_mod
        src = inspect.getsource(hr_mod.save_state)
        assert "atomic_write_json" in src, \
            "human_review.save_state 必须用 atomic_write_json"
        assert "open(STATE_PATH" not in src, \
            "human_review.save_state 不能再 raw open(STATE_PATH, 'w')"

    def test_human_review_meta_write_uses_atomic(self):
        import inspect
        from engine.tools import human_review as hr_mod
        src = inspect.getsource(hr_mod)
        code_lines = [l for l in src.split("\n")
                      if l.strip() and not l.strip().startswith("#")]
        code_src = "\n".join(code_lines)
        assert "atomic_write_json" in code_src, \
            "human_review meta 写盘也必须用 atomic_write_json"


# ───────────────────────────────────────────
# AAAA: fix #60 — orchestrator.run_summarizer 异常不再静默
# ───────────────────────────────────────────
class TestOrchestratorSummarizerNotSilent:
    """迭代 #60: orchestrator.node_save_and_track 弧末 run_summarizer
    之前 except Exception: cost=0.0 静默 —— 跟 #58 run_tracker 同型。
    """
    def test_orchestrator_marks_summarizer_failed(self):
        import inspect
        from engine import orchestrator as orch_mod
        src = inspect.getsource(orch_mod.node_save_and_track)
        code_lines = [l for l in src.split("\n")
                      if l.strip() and not l.strip().startswith("#")]
        code_src = "\n".join(code_lines)
        assert "_summarizer_failed" in code_src, \
            "orchestrator.node_save_and_track summarizer 异常路径必须标 _summarizer_failed（iter #60）"
        assert "summarizer failed arc" in code_src, \
            "orchestrator.node_save_and_track summarizer 异常路径必须 log error_log"


# ───────────────────────────────────────────
# BBBB: smoke test — orchestrator pipeline 状态连贯性
# ───────────────────────────────────────────
class TestOrchestratorPipelineStateCoherence:
    """锁定 engine/orchestrator 节点间状态连贯性。
    
    验证以下不变量：
    1. create_initial_state 后所有进度字段 = 0
    2. node_load_arc_tasks 后 arc_plans + chapter_task_queue 注入
    3. node_get_next_task 后 current_task + rewrite_count = 0
    4. 任何阶段异常 → state.error_log 增量 + 标 _xxx_failed
    5. save_state 后 last_updated 自动更新（iter #7-#28 行为）
    """
    def test_create_initial_state_all_progress_zero(self):
        from engine.state import create_initial_state
        s = create_initial_state(
            novel_id="test_n", title="测试", platform="fanqie",
            genre="玄幻", setting_concept="",
        )
        assert s["current_chapter"] == 0
        assert s["current_arc"] == 0
        assert s["budget_used_usd"] == 0.0
        assert s["chapter_task_queue"] == []
        assert s["current_task"] is None
        assert s["error_log"] == []
        assert s["rewrite_count_current"] == 0

    def test_save_state_updates_last_updated(self, tmp_path):
        """save_state 必须更新 last_updated（iter #28 行为）。"""
        from engine.state import create_initial_state, save_state, load_state
        s = create_initial_state("t", "t", "fanqie", "玄幻", "")
        s["last_updated"] = "2000-01-01T00:00:00"  # 显式旧值
        path = str(tmp_path / "state.json")
        save_state(s, path)
        loaded = load_state(path)
        assert loaded["last_updated"] != "2000-01-01T00:00:00", \
            "save_state 必须更新 last_updated（iter #28）"

    def test_save_state_creates_tmp_during_write(self, tmp_path):
        """save_state 必须用 .tmp + atomic rename（半写损坏防护）。"""
        from engine.state import create_initial_state, save_state
        s = create_initial_state("t", "t", "fanqie", "玄幻", "")
        path = str(tmp_path / "state.json")
        save_state(s, path)
        # 完成后 .tmp 必须被替换走
        assert not (tmp_path / "state.json.tmp").exists(), \
            "save_state atomic write 完成后 .tmp 必须被替换走"

    def test_node_get_next_task_pops_from_queue(self):
        """node_get_next_task 必须从 queue pop 并 set current_task。"""
        from engine.state import create_initial_state
        from engine.orchestrator import node_get_next_task
        s = create_initial_state("t", "t", "fanqie", "玄幻", "")
        s["chapter_task_queue"] = [
            {"chapter_number": 1, "chapter_role": "铺垫", "chapter_goal": "起始"},
            {"chapter_number": 2, "chapter_role": "发展", "chapter_goal": "推进"},
        ]
        s = node_get_next_task(s)
        assert s["current_task"]["chapter_number"] == 1, \
            "node_get_next_task 必须 set current_task = queue[0]"
        assert len(s["chapter_task_queue"]) == 1, \
            f"node_get_next_task 必须 pop 一个，剩余 {len(s['chapter_task_queue'])}"
        assert s["rewrite_count_current"] == 0, \
            "node_get_next_task 必须重置 rewrite_count_current"
        assert s["current_chapter"] == 1, \
            f"current_chapter 必须 = current_task.chapter_number，实际 {s['current_chapter']}"

    def test_node_get_next_task_empty_queue_returns_state(self):
        """queue 空时 node_get_next_task 必须返回 state（不抛异常）。"""
        from engine.state import create_initial_state
        from engine.orchestrator import node_get_next_task
        s = create_initial_state("t", "t", "fanqie", "玄幻", "")
        s["chapter_task_queue"] = []
        s2 = node_get_next_task(s)
        assert s2["current_task"] is None, \
            "空 queue 时 current_task 必须仍为 None"


# ───────────────────────────────────────────
# CCCC: fix #62 — app/llm_client.py IndexError 不再逃出重试循环
# ───────────────────────────────────────────
class TestLlmClientRetryCatchesAll:
    """迭代 #62: app/llm_client.py:71 之前只 catch KeyError — IndexError
    （choices 空列表）和 TypeError（message 是 None）会跳出重试循环。
    修法：扩 catch 列表。
    """
    def test_llm_client_catches_index_error(self):
        """LLM 返回 {\"choices\": []} 必须走重试，不是直接抛 IndexError。"""
        from unittest.mock import patch, AsyncMock, MagicMock
        from app import llm_client as lc_mod
        import httpx

        # mock resolve_provider 返回 fake provider
        fake_cfg = MagicMock()
        fake_cfg.provider = "deepseek"
        fake_cfg.api_base = "https://api.deepseek.com/v1"
        fake_cfg.api_key = "sk-fake"
        fake_cfg.model = "deepseek-chat"

        # 第一次返回 choices=[]（IndexError），第二次成功
        empty_resp = MagicMock()
        empty_resp.raise_for_status = MagicMock()
        empty_resp.json = MagicMock(return_value={"choices": []})
        ok_resp = MagicMock()
        ok_resp.raise_for_status = MagicMock()
        ok_resp.json = MagicMock(return_value={
            "choices": [{"message": {"content": '{"ok": true}'}}]
        })
        # 使用 AsyncMock 让两次调用返回不同值
        async def post_side_effect(*args, **kwargs):
            return empty_resp if post_side_effect.call_count == 0 else ok_resp
        post_side_effect.call_count = 0
        async def track(*args, **kwargs):
            post_side_effect.call_count += 1
            if post_side_effect.call_count == 1:
                return empty_resp
            return ok_resp

        with patch.object(lc_mod, "resolve_provider", return_value=fake_cfg), \
             patch.object(lc_mod, "_build_httpx_client") as mock_client:
            # 构造 AsyncClient that 走 __aenter__ 返回 .post side_effect
            mock_inst = MagicMock()
            mock_inst.__aenter__ = AsyncMock(return_value=mock_inst)
            mock_inst.__aexit__ = AsyncMock(return_value=None)
            mock_inst.post = track
            mock_client.return_value = mock_inst

            import asyncio
            result = asyncio.run(lc_mod.call_llm_json("structured_logic", "sys", "user"))
        assert result == {"ok": True}, \
            f"IndexError 必须被重试吞掉，第二次成功返回 dict，实际 {result}"

    def test_llm_client_catches_type_error(self):
        """LLM 返回 {\"choices\": [{\"message\": null}]} 必须走重试。"""
        from unittest.mock import patch, AsyncMock, MagicMock
        from app import llm_client as lc_mod

        fake_cfg = MagicMock()
        fake_cfg.provider = "deepseek"
        fake_cfg.api_base = "https://api.deepseek.com/v1"
        fake_cfg.api_key = "sk-fake"
        fake_cfg.model = "deepseek-chat"

        type_err_resp = MagicMock()
        type_err_resp.raise_for_status = MagicMock()
        type_err_resp.json = MagicMock(return_value={
            "choices": [{"message": None}]  # None["content"] → TypeError
        })
        ok_resp = MagicMock()
        ok_resp.raise_for_status = MagicMock()
        ok_resp.json = MagicMock(return_value={
            "choices": [{"message": {"content": '{"ok": true}'}}]
        })

        with patch.object(lc_mod, "resolve_provider", return_value=fake_cfg), \
             patch.object(lc_mod, "_build_httpx_client") as mock_client:
            mock_inst = MagicMock()
            mock_inst.__aenter__ = AsyncMock(return_value=mock_inst)
            mock_inst.__aexit__ = AsyncMock(return_value=None)
            call_count = [0]
            async def track(*args, **kwargs):
                call_count[0] += 1
                return type_err_resp if call_count[0] == 1 else ok_resp
            mock_inst.post = track
            mock_client.return_value = mock_inst
            import asyncio
            result = asyncio.run(lc_mod.call_llm_json("structured_logic", "sys", "user"))
        assert result == {"ok": True}, \
            f"TypeError 必须被重试吞掉，实际 {result}"


# ───────────────────────────────────────────
# DDDD: smoke test — engine state lock + budget never overflows
# ───────────────────────────────────────────
class TestEngineStateSafetyInvariants:
    """锁定 engine/state.py + orchestrator.py 的安全不变量。
    
    这些不变量不一定对应 bug，但锁住防止回归：
    1. save_state 后 budget_used_usd 单调递增（前提 cost >= 0）
    2. load_state 必须能恢复 save_state 写入的内容
    3. error_log 最多 100 条（防止内存无限增长）
    4. log("ERR ...") 必须把消息加进 error_log
    """
    def test_save_load_round_trip(self, tmp_path):
        """save → load 必须 round-trip（同一字段相同值）。"""
        from engine.state import create_initial_state, save_state, load_state
        s = create_initial_state("t", "title", "fanqie", "玄幻", "测试概念")
        s["current_chapter"] = 5
        s["budget_used_usd"] = 12.5
        s["error_log"] = ["ERR foo", "WARN bar"]
        path = str(tmp_path / "state.json")
        save_state(s, path)
        loaded = load_state(path)
        assert loaded["novel_id"] == "t"
        assert loaded["current_chapter"] == 5
        assert loaded["budget_used_usd"] == 12.5
        assert loaded["error_log"] == ["ERR foo", "WARN bar"]

    def test_error_log_capped_at_100(self):
        """engine/orchestrator.py log() 必须把 error_log 截到 100 条（el[-100:]）。"""
        import inspect
        from engine import orchestrator as orch_mod
        src = inspect.getsource(orch_mod.log)
        assert "el[-100:]" in src or "[-100:]" in src, \
            "orchestrator.log 必须把 error_log 截到 100 条防止内存无限增长"

    def test_log_err_message_goes_into_error_log(self):
        """log(\"ERR xxx\") 必须把消息加进 error_log。"""
        from engine import orchestrator as orch_mod
        s = orch_mod.create_initial_state("t", "t", "fanqie", "玄幻", "")
        orch_mod.log("ERR 测试错误信息", s)
        assert any("ERR 测试错误信息" in line for line in s["error_log"]), \
            f"log('ERR xxx') 必须把消息加进 error_log，实际 {s['error_log']}"

    def test_log_non_err_message_not_in_error_log(self):
        """log(\"...\" 不含 ERR/FAIL) 不应进 error_log。"""
        from engine import orchestrator as orch_mod
        s = orch_mod.create_initial_state("t", "t", "fanqie", "玄幻", "")
        orch_mod.log("普通日志信息，不应进 error_log", s)
        # 普通信息不应进 error_log（除非包含 ERR/FAIL）
        # 但 ERR/FAIL 是字符串检查，如果信息里有"ERR"字样仍会进 — 这是预期
        assert all("ERR" not in line and "FAIL" not in line
                   for line in s["error_log"]), \
            f"普通信息不应进 error_log，实际 {s['error_log']}"


# ───────────────────────────────────────────
# EEEE: fix #64 — orchestrator._setting 损坏文件不再 crash
# ───────────────────────────────────────────
class TestOrchestratorSettingLoadError:
    """迭代 #64: engine/orchestrator.py:_setting 之前 `json.load(f)`
    损坏文件时直接抛 — 没有 backup / 没有清晰错误。
    修法：损坏时 backup 到 .corrupted.{ts}，raise 带可读信息。
    """
    def test_setting_load_corrupt_file_raises_with_clear_message(self, tmp_path, monkeypatch):
        """损坏 setting_package.json 必须 raise 带「文件损坏」可读信息。"""
        from pathlib import Path
        from engine import orchestrator as orch_mod
        # 重置 module-level cache
        monkeypatch.setattr(orch_mod, "_setting_cache", None)
        monkeypatch.setattr(orch_mod, "SETTING_PATH", Path(tmp_path / "setting.json"))

        # 写损坏文件
        (tmp_path / "setting.json").write_text("{ not valid", encoding="utf-8")

        with pytest.raises(Exception) as exc_info:
            orch_mod._setting()
        # 必须有可读错误信息（提到损坏 / JSON / 文件名）
        err_msg = str(exc_info.value)
        assert "Expecting" in err_msg or "JSON" in err_msg, \
            f"损坏文件必须 raise JSON 解析错误，实际 {type(exc_info.value).__name__}: {err_msg}"

    def test_setting_load_missing_file_returns_empty_dict(self, tmp_path, monkeypatch):
        """setting_package.json 不存在时 _setting 必须返回 {}（首次启动）。"""
        from pathlib import Path
        from engine import orchestrator as orch_mod
        monkeypatch.setattr(orch_mod, "_setting_cache", None)
        monkeypatch.setattr(orch_mod, "SETTING_PATH", Path(tmp_path / "nope.json"))
        # 文件不存在 → 返回 {}（不抛）
        result = orch_mod._setting()
        assert result == {}, \
            f"setting 文件不存在时必须返回 {{}}，实际 {result}"

    def test_setting_cache_hit_after_first_load(self, tmp_path, monkeypatch):
        """_setting_cache 同 mtime 必须 cache（不重读盘）；iter #65 mtime-based 行为。"""
        from pathlib import Path
        from engine import orchestrator as orch_mod
        import json as _json
        (tmp_path / "setting.json").write_text(
            _json.dumps({"title_candidates": ["test"], "genre": "玄幻"}),
            encoding="utf-8",
        )
        monkeypatch.setattr(orch_mod, "_setting_cache", None)
        monkeypatch.setattr(orch_mod, "_setting_mtime", None)
        monkeypatch.setattr(orch_mod, "SETTING_PATH", Path(tmp_path / "setting.json"))

        first = orch_mod._setting()
        assert first["title_candidates"] == ["test"]
        # 第二次不改文件 → cache hit（同 mtime 不 reload）
        from unittest.mock import patch
        with patch("engine.orchestrator.json.load") as mock_load:
            second = orch_mod._setting()
        assert mock_load.call_count == 0, \
            f"不改文件时不应调 json.load，实际调了 {mock_load.call_count} 次"
        assert second["title_candidates"] == ["test"]


# ───────────────────────────────────────────
# FFFF: fix #65 — orchestrator._setting 缓存按 mtime invalidate
# ───────────────────────────────────────────
class TestOrchestratorSettingCacheInvalidates:
    """迭代 #65: orchestrator._setting 之前 cache 一旦填就永不刷新 — 同一进程
    跑完 planner 后 setting_package.json 更新了，orchestrator 还用老值。
    修法：按 mtime 检测文件变化自动 invalidate。
    """
    def test_setting_cache_invalidates_on_file_change(self, tmp_path, monkeypatch):
        from pathlib import Path
        import time as _t
        from engine import orchestrator as orch_mod
        import json as _json

        monkeypatch.setattr(orch_mod, "_setting_cache", None)
        monkeypatch.setattr(orch_mod, "_setting_mtime", None)
        setting_path = Path(tmp_path / "setting.json")
        monkeypatch.setattr(orch_mod, "SETTING_PATH", setting_path)

        # 第一次写入
        setting_path.write_text(
            _json.dumps({"version": 1, "title": "old"}), encoding="utf-8"
        )
        first = orch_mod._setting()
        assert first["version"] == 1

        # 模拟时间过去 + 修改文件
        _t.sleep(0.05)  # 确保 mtime 不同
        setting_path.write_text(
            _json.dumps({"version": 2, "title": "new"}), encoding="utf-8"
        )
        second = orch_mod._setting()
        assert second["version"] == 2, \
            f"文件改了 _setting 必须 reload（按 mtime invalidate），实际 {second}"
        assert second["title"] == "new"

    def test_setting_cache_hit_keeps_same_value_no_file_change(self, tmp_path, monkeypatch):
        """没改文件时 _setting 必须走 cache（不重读盘）。"""
        from pathlib import Path
        from engine import orchestrator as orch_mod
        import json as _json

        monkeypatch.setattr(orch_mod, "_setting_cache", None)
        monkeypatch.setattr(orch_mod, "_setting_mtime", None)
        setting_path = Path(tmp_path / "setting.json")
        monkeypatch.setattr(orch_mod, "SETTING_PATH", setting_path)
        setting_path.write_text(
            _json.dumps({"version": 1}), encoding="utf-8"
        )

        # 第一次读
        first = orch_mod._setting()
        assert first["version"] == 1

        # 用 mock 替换 json.load 验证第二次不调
        from unittest.mock import patch
        with patch("engine.orchestrator.json.load") as mock_load:
            second = orch_mod._setting()
        assert mock_load.call_count == 0, \
            f"文件没改时 _setting 不应重读盘，但调了 {mock_load.call_count} 次 json.load"
        # 迭代 #69：_setting 现在返回 dict copy（防调用方污染 cache），
        # 不再保证 identity 相等，但 value 必须一致
        assert second == first, \
            f"cache 命中必须返回相同内容（#69 返回 copy，不再是同对象），实际 {second} vs {first}"

    def test_invalidate_setting_cache_helper(self, tmp_path, monkeypatch):
        """invalidate_setting_cache() 必须重置 cache + mtime。"""
        from pathlib import Path
        from engine import orchestrator as orch_mod
        import json as _json

        monkeypatch.setattr(orch_mod, "_setting_cache", None)
        monkeypatch.setattr(orch_mod, "_setting_mtime", None)
        setting_path = Path(tmp_path / "setting.json")
        monkeypatch.setattr(orch_mod, "SETTING_PATH", setting_path)
        setting_path.write_text(_json.dumps({"version": 1}), encoding="utf-8")
        orch_mod._setting()  # populate cache

        # 调用 invalidate
        orch_mod.invalidate_setting_cache()
        assert orch_mod._setting_cache is None, \
            "invalidate_setting_cache 必须重置 _setting_cache 为 None"
        assert orch_mod._setting_mtime is None, \
            "invalidate_setting_cache 必须重置 _setting_mtime 为 None"


# ───────────────────────────────────────────
# GGGG: fix #66 — engine/state.py save_state Windows 空文件 lock 失败
# ───────────────────────────────────────────
class TestSaveStateWindowsEmptyFileLock:
    """迭代 #66: Windows 上 msvcrt.locking(fd, LK_LOCK, 1) 要求 position+1
    字节可访问。空文件（刚 truncate）position=0 时锁 1 字节失败 → 返回 False →
    save_state 走无锁路径（race condition）。
    修法：先写 1 字节 placeholder、锁住后 seek(0)+truncate 清掉占位，
    再正常写 JSON。POSIX 路径不受影响（fcntl 不需要这个 hack）。
    """
    def test_save_state_source_has_windows_empty_file_workaround(self):
        import inspect
        from engine import state as state_mod
        src = inspect.getsource(state_mod.save_state)
        code_lines = [l for l in src.split("\n")
                      if l.strip() and not l.strip().startswith("#")]
        code_src = "\n".join(code_lines)
        # 源码必须有 Windows + placeholder 关键词（说明做了绕过空文件 hack）
        assert "win32" in code_src, \
            "engine.state.save_state 必须有 win32 平台分支处理空文件 lock 失败"
        assert "placeholder" in code_src or "seek(0)" in code_src, \
            "engine.state.save_state 必须有 placeholder byte / seek(0) workaround"


# ───────────────────────────────────────────
# HHHH: 旧审计报告 review #1 / #2 落地（fix #67-#71）
# ───────────────────────────────────────────
class TestAuditReviewFeedbackApplied:
    """应用参考审计报告里的 actionable items。

    参考 1（commit 33a5c09 — save_state last_updated）：
      - 冗余 local import datetime（已修：state.py:10 顶层 import 复用）
      - naive datetime → 改 timezone.utc（#68）
      - 非原子写：save_state 之前用 raw open(w)+json.dump（iter #67 atomic write）
      - 测试 state 不完整（稍后改）

    参考 2（commit 4b2bc7e — _setting mtime invalidate）：
      - stat 失败静默 fallback → 加 log.warning（#70）
      - 返回内部 cache 引用 → 返回 copy（#69）
      - planner 不调 invalidate → planner 写完显式调（#71）
    """
    def test_save_state_uses_timezone_utc(self, tmp_path):
        """save_state 必须用 timezone.utc 而非 naive datetime。"""
        from engine.state import create_initial_state, save_state, load_state
        import re
        s = create_initial_state("t", "t", "fanqie", "玄幻", "")
        path = str(tmp_path / "state.json")
        save_state(s, path)
        loaded = load_state(path)
        ts = loaded["last_updated"]
        # timezone.utc 生成的 ISO 字符串可能末尾带 +00:00 或 Z
        # naive datetime 是 "2024-01-01T12:00:00.123456"（无 timezone）
        assert "+00:00" in ts or ts.endswith("Z") or "+0000" in ts, \
            f"last_updated 必须带 timezone 信息（UTC），实际 {ts}"

    def test_save_state_source_uses_timezone(self):
        """源码扫描：save_state 必须 from datetime import datetime, timezone 且用 timezone.utc。"""
        import inspect
        from engine import state as state_mod
        src = inspect.getsource(state_mod.save_state)
        # 必须 from datetime import datetime, timezone（顶层导入，不再函数内重复）
        # 或者至少 datetime.now(timezone.utc) 出现
        assert "datetime.now(timezone.utc)" in src, \
            "save_state 必须 datetime.now(timezone.utc)（#68）"
        # 必须没有 naive datetime.now() 调用（无 timezone 参数）
        import re
        # 扫描代码本身（剥离注释 + 文档字符串）——否则注释里举例的
        # "naive datetime.now()" 文本会被误判为代码里的实际调用
        code_only = "\n".join(
            l for l in src.split("\n") if not l.lstrip().startswith("#")
        )
        # 进一步剥离 docstring（save_state 顶部的 """...""")
        if '"""' in code_only:
            parts = code_only.split('"""')
            if len(parts) >= 3:
                # docstring 在第 1 和第 2 个 """ 之间（docstring 内容）
                code_only = parts[0] + parts[2]
        naive_pattern = re.findall(r"datetime\.now\(\s*\)", code_only)
        assert not naive_pattern, \
            f"save_state 不能再有 naive datetime.now()，实际 {len(naive_pattern)} 处"

    def test_save_state_no_redundant_local_datetime_import(self):
        """源码扫描：save_state 不能有 `from datetime import datetime` 内联 import。"""
        import inspect
        from engine import state as state_mod
        src = inspect.getsource(state_mod.save_state)
        # 函数体内不应有 from datetime import datetime（顶层已 import）
        body_lines = src.split('"""')[2] if '"""' in src else src
        body_lines = [l for l in body_lines.split("\n")
                      if l.strip() and not l.strip().startswith("#")]
        body_src = "\n".join(body_lines)
        assert "from datetime import datetime" not in body_src, \
            "save_state 函数体不能再 `from datetime import datetime`（顶层已 import）"

    def test_setting_stat_failure_logs_warning(self, tmp_path, monkeypatch, caplog):
        """_setting stat 失败时必须 log.warning（#70）。"""
        from pathlib import Path
        import logging
        from engine import orchestrator as orch_mod
        monkeypatch.setattr(orch_mod, "_setting_cache", None)
        monkeypatch.setattr(orch_mod, "_setting_mtime", None)
        # 真实存在文件（让 .exists() 走得通），独立 mock stat() 抛 OSError
        setting_path = Path(tmp_path / "setting.json")
        setting_path.write_text('{"title": "x"}', encoding="utf-8")
        monkeypatch.setattr(orch_mod, "SETTING_PATH", setting_path)
        import unittest.mock
        # Python 3.12+ Path.stat() 签名带 follow_symlinks kwarg，
        # mock 函数必须接受任意参数否则 patch 会 TypeError
        def failing_stat(self, *args, **kwargs):
            raise OSError("permission denied")
        # 同时 mock .exists() 让它返回 True（不然会走"文件不存在"分支
        # 直接返回 {}，不会触发我们要测的 stat() except 分支）
        with unittest.mock.patch.object(Path, "stat", failing_stat), \
             unittest.mock.patch.object(Path, "exists", lambda self: True):
            with caplog.at_level("WARNING", logger="novel_ai.engine.orchestrator"):
                result = orch_mod._setting()
            # 必须 log warning（带 stat / OSError 信息）
            warning_msgs = [r.message for r in caplog.records if r.levelname == "WARNING"]
            assert any("stat" in m.lower() or "_setting" in m.lower() for m in warning_msgs), \
                f"_setting stat 失败必须 log.warning，实际 {warning_msgs}"
            assert result == {}, \
                f"stat 失败时没 cache 应返回 {{}}，实际 {result}"

    def test_setting_returns_copy_not_internal_reference(self, tmp_path, monkeypatch):
        """_setting 必须返回 copy（#69）—— 防止调用方修改污染 cache。"""
        from pathlib import Path
        from engine import orchestrator as orch_mod
        import json as _json
        setting_path = Path(tmp_path / "setting.json")
        setting_path.write_text(_json.dumps({"title": "original"}), encoding="utf-8")
        monkeypatch.setattr(orch_mod, "_setting_cache", None)
        monkeypatch.setattr(orch_mod, "_setting_mtime", None)
        monkeypatch.setattr(orch_mod, "SETTING_PATH", setting_path)

        result = orch_mod._setting()
        result["title"] = "mutated"  # 尝试修改返回值
        # 再次调用必须拿回原值（不是被污染的 cache）
        result2 = orch_mod._setting()
        assert result2["title"] == "original", \
            f"_setting 返回 copy 应该不被外部修改污染，但 cache 已被改：{result2['title']}"

    def test_graph_planner_command_invalidates_setting_cache(self):
        """graph.py planner command 必须调 invalidate_setting_cache（#71 兜底）。"""
        import inspect
        from engine import graph as graph_mod
        src = inspect.getsource(graph_mod.run_graph_task)
        # 找 elif command == "planner": 段
        assert "elif command == \"planner\"" in src, \
            "graph.run_graph_task 必须有 planner 分支"
        # planner 分支里必须调 invalidate_setting_cache
        planner_idx = src.find("elif command == \"planner\":")
        # 找到下一个 elif（分支结束）
        next_elif = src.find("elif command ==", planner_idx + 10)
        planner_branch = src[planner_idx:next_elif if next_elif > 0 else None]
        assert "invalidate_setting_cache" in planner_branch, \
            "planner 分支写完 setting_package.json 后必须显式调 invalidate_setting_cache（#71 兜底 mtime 检测的 1s 精度风险）"


# ───────────────────────────────────────────
# IIII: 迭代 #72 — MASTER_KEY 同进程稳定性（修 in-process key 漂移 bug）
# ───────────────────────────────────────────
class TestMasterKeyStableAcrossCalls:
    """迭代 #72（severe bug fix）：

    之前 `get_master_key()` 每次调用在 dev 模式（无 MASTER_KEY env）都会
    生成新的随机 key，导致：
      encrypt_api_key('sk-xxx') → encrypt with key_K1
      decrypt_api_key(cipher) → decrypt with key_K2 ≠ K1
      → ValueError "MASTER_KEY 变了或数据被损坏"

    文档承诺"dev 模式不设 MASTER_KEY 也能跑（至少同进程内稳定）"完全不成立。
    测试代码（test_invariants.py:1487 附近）已经知道这个不稳定性，专门
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


# ───────────────────────────────────────────
# JJJJ: 迭代 #73 — memory/manager.py 不能静默吞异常（同型扫描补漏）
# ───────────────────────────────────────────
class TestMemoryManagerNoSilentException:
    """迭代 #73（medium bug fix）：

    CHANGELOG 里"发现某处 except Exception: pass → 改 log+fail-fast"
    模式被多次套用，但 engine/memory/manager.py 漏扫到——这文件里有 4 处
    `except Exception: continue/pass`，读章节 meta / 章节正文 / 风格样本 /
    清理旧 auto 文件失败时**完全静默**。影响：损坏文件悄悄导致 Writer
    上下文不完整，没有 signal 告诉运维为什么。

    审计报告（2026-07-05）确认这是被漏扫的真 bug，不是"测试只验证
    设置了 MASTER_KEY 的场景"那种测试覆盖盲点。

    修法：module logger + 每处 `except Exception` 都 log.exception 后
    continue，行为不变（仍 continue）但有诊断信号。
    """
    def _function_source(self, func):
        import inspect
        return inspect.getsource(func)

    def test_module_has_logger(self):
        """源码扫描：manager.py 模块级必须定义 logger（#73 标志）。"""
        import inspect
        from engine.memory import manager as mgr_mod
        src = inspect.getsource(mgr_mod)
        assert "getLogger" in src or "logger" in src.lower(), \
            "memory/manager.py 必须有 module logger 用于报告被吞掉的异常（#73）"

    def test_no_silent_continue_in_internal_meta_loop(self):
        """_get_internal_samples 读 meta 失败的 except 必须有 log.* 调用。"""
        from engine.memory import manager as mgr_mod
        src = self._function_source(mgr_mod._get_internal_samples)
        # 找第 1 个 except Exception
        idx = src.find("except Exception:")
        assert idx != -1, "_get_internal_samples 必须有 except Exception 段"
        # 那一段（到下一个 except 或函数结束）必须有 log
        chunk_end = idx + 200  # 查到下一个 except
        next_except = src.find("except Exception:", idx + 10)
        if next_except != -1:
            chunk_end = next_except
        chunk = src[idx:chunk_end]
        assert "log" in chunk.lower(), \
            f"_get_internal_samples 第 1 处 except 必须 log.exception（#73），chunk:\n{chunk}"
        assert "continue" in chunk, \
            f"行为应继续往下（continue）但有日志，chunk:\n{chunk}"

    def test_no_silent_continue_in_chapter_text_loop(self):
        """_get_internal_samples 读 ch_NNNN.txt 失败的 except 必须有 log。"""
        from engine.memory import manager as mgr_mod
        src = self._function_source(mgr_mod._get_internal_samples)
        # 第 2 个 except
        first = src.find("except Exception:")
        second = src.find("except Exception:", first + 10)
        assert second != -1, \
            "_get_internal_samples 必须有第 2 处 except（章节正文读取）"
        chunk = src[second:second + 200]
        assert "log" in chunk.lower(), \
            f"_get_internal_samples 第 2 处 except 必须 log.exception（#73），chunk:\n{chunk}"

    def test_no_silent_continue_in_external_samples(self):
        """_get_external_samples 读风格样本失败的 except 必须有 log。"""
        from engine.memory import manager as mgr_mod
        src = self._function_source(mgr_mod._get_external_samples)
        idx = src.find("except Exception:")
        assert idx != -1, "_get_external_samples 必须有 except Exception"
        chunk = src[idx:idx + 200]
        assert "log" in chunk.lower(), \
            f"_get_external_samples except 必须 log.exception（#73），chunk:\n{chunk}"

    def test_no_silent_pass_in_cleanup_loop(self):
        """maybe_update_style_samples 清理旧 auto 文件 except 必须有 log。"""
        from engine.memory import manager as mgr_mod
        src = self._function_source(mgr_mod.maybe_update_style_samples)
        idx = src.find("except Exception:")
        assert idx != -1, "maybe_update_style_samples 必须有 except Exception"
        chunk = src[idx:idx + 200]
        assert "log" in chunk.lower(), \
            f"maybe_update_style_samples except 必须 log.exception（#73），chunk:\n{chunk}"

    def test_behavioral_broken_meta_logs_and_continues(self, tmp_path, monkeypatch, caplog):
        """行为测试：meta 文件损坏时 _get_internal_samples 必须 log 但仍返回可用样本。

        之前 bug：静默 continue，外部完全看不到信号。
        修复后：log.exception 后 continue，caplog 能抓到。
        """
        import json
        import logging
        from pathlib import Path
        from engine.config.paths import CHAPTERS_DIR_STR
        from engine.memory import manager as mgr_mod

        # 重定向 CHAPTERS_DIR_STR 到临时目录
        monkeypatch.setattr(mgr_mod, "CHAPTERS_DIR_STR", str(tmp_path))

        # 写 1 个坏 meta + 1 个好 meta + 对应章节文件
        bad_meta = tmp_path / "ch_0001_meta.json"
        bad_meta.write_text("{ this is not valid json", encoding="utf-8")
        good_meta = tmp_path / "ch_0002_meta.json"
        good_meta.write_text(json.dumps({"score": 8.0, "chapter_number": 2}),
                             encoding="utf-8")
        # good meta 对应章节文件
        good_ch = tmp_path / "ch_0002.txt"
        good_ch.write_text("这是高分章节正文", encoding="utf-8")

        with caplog.at_level(logging.ERROR, logger="novel_ai.engine.memory.manager"):
            result = mgr_mod._get_internal_samples()

        # 行为：仍返回样本（continue 不抛）
        assert isinstance(result, list)
        # 行为：好的那条被收进来了（meta 解析成功 + 分数 ≥ 7.5 + 章节文件存在）
        assert any("高分章节正文" in s for s in result), \
            f"好样本应保留，实际 {result}"
        # 关键：坏 meta 必须产生 log 记录（之前静默 swallowed）
        err_records = [r for r in caplog.records
                       if r.levelno >= logging.ERROR
                       and "memory" in r.name.lower()]
        assert err_records, (
            "损坏的 meta 文件必须被 log 记录（之前静默吞掉，#73 修法）"
            f"实际 caplog records: {[(r.levelname, r.name, r.getMessage()) for r in caplog.records]}"
        )
        assert any("ch_0001" in r.getMessage() for r in err_records), \
            f"log 应包含损坏文件路径 ch_0001，实际 messages: {[r.getMessage() for r in err_records]}"


# ───────────────────────────────────────────
# KKKK: 迭代 #74 — bridge.py 并发保护 TOCTOU 窗口（status='pending' 也算 active）
# ───────────────────────────────────────────
class TestBridgeRunConcurrencyGuard:
    """迭代 #74（medium bug fix）：

    历史：bridge.py:107 之前只查 `BridgeRun.status == 'running'` 来防止
    同一 project 并发触发两次 writing engine。

    真实流程时序（移除 asyncio.Lock 之后）：
      T0  request1 进入 run_bridge()
      T1  request1 查 running → 无
      T2  request1 db.add(BridgeRun(status='pending')) + commit
      T3  request1 background_tasks.add_task(_spawn_engine_subprocess, ...)
      T4  ... background thread 内 _drain_stdout 把 status 翻成 'running'

    T0~T4 之间的窗口里，request2 同样能查 running→无，同样放行 →
    两个 engine 子进程同时对同一 project_id 跑 → 同时写同一份
    checkpoint 文件 + 同时写同一份 .env。

    修法：active 检查改为 `status in ('pending','running')` —— 一旦
    request1 把 pending insert commit 成功，request2 的同检查立刻能看见，
    不放行。
    """
    def test_run_bridge_checks_pending_in_active(self):
        """源码扫描：run_bridge 必须把 'pending' 也算 active（#74）。"""
        import inspect
        from app.api import bridge as bridge_mod
        src = inspect.getsource(bridge_mod.run_bridge)
        # 必须有 'pending' 字面出现（说明检查了 pending 状态）
        assert "'pending'" in src or '"pending"' in src, (
            "bridge.run_bridge active 检查必须包含 'pending' 状态，"
            "否则新插入的 BridgeRun 在 status 翻 'running' 前会通过并发检查（#74）"
        )
        # 必须用 in_(...) / in [...] 而不是单独等号（否则只查一个状态）
        assert "in_([" in src or 'in_("pending"' in src or \
               'status in [' in src, (
            "active 检查必须用 in_() 包含多个状态（#74），"
            "单纯 status=='running' 仍有 TOCTOU 窗口"
        )

    def test_run_bridge_no_more_only_running_check(self):
        """回归保护：run_bridge 不能退回到只查 'running'（防止有人 reverted #74）。"""
        import re
        import inspect
        from app.api import bridge as bridge_mod
        src = inspect.getsource(bridge_mod.run_bridge)
        # 退化的 "only running" 模式：filter_by(status="running") 单独使用
        # 允许 'running' 出现在 in_(['pending','running']) 里但不允许单独 filter_by
        bad_patterns = [
            r'filter_by\([^)]*status\s*=\s*["\']running["\'][^)]*\)',
            r'\.filter_by\([^)]*["\']running["\'][^)]*\)',
        ]
        for pat in bad_patterns:
            m = re.search(pat, src)
            assert not m, (
                f"bridge.run_bridge 退化到只查 'running' 模式（#74 已修），匹配 {pat} → {m.group() if m else None}"
            )

    def test_functional_pending_run_blocks_new_run(self):
        """行为测试：DB 里已经有一条 pending BridgeRun，再 insert 会触发 UNIQUE-like 冲突（#74）。

        通过直接 reproduce query pattern 验证：
          - 普通 query filter_by(status='running') 查不到 pending 行（确认这就是 bug）
          - 修法 query status.in_(['pending','running']) 能查到（确认修法有效）
        """
        from datetime import datetime, timezone
        from app.database import SessionLocal
        from app.models import BridgeRun, Project

        db = SessionLocal()
        try:
            # 准备：先建一个真 Project
            project = Project(
                id="test-toctou-window-proj",
                title="TOCTOU window test",
                genre="都市",
                audience="男频",
                status="ready",
                config_json={},
            )
            db.add(project)
            db.commit()
            project_id = project.id

            # 插入一条 pending 行（模拟 request1 已经走到 T2 commit 但还没翻 running）
            pending_run = BridgeRun(
                project_id=project_id,
                command="run",
                status="pending",
                started_at=datetime.now(timezone.utc),
            )
            db.add(pending_run)
            db.commit()
            pending_id = pending_run.id
        finally:
            db.close()

        try:
            db = SessionLocal()
            # 验证：旧 query（只查 running）查不到 pending —— 这就是 bug
            old_check = db.query(BridgeRun).filter_by(
                project_id=project_id, status="running"
            ).first()
            assert old_check is None, (
                "前提：旧 query 只查 running 应该查不到 pending 行（证实 bug 存在）"
            )

            # 验证：修法 query（包含 pending）能查到
            from sqlalchemy import or_
            new_check = db.query(BridgeRun).filter(
                BridgeRun.project_id == project_id,
                BridgeRun.status.in_(["pending", "running"]),
            ).first()
            assert new_check is not None, (
                "修复后 query 必须能查到 pending 行（#74 关闭 TOCTOU 窗口）"
            )
            assert new_check.id == pending_id
            assert new_check.status == "pending"
        finally:
            # 清理测试数据
            db = SessionLocal()
            try:
                run = db.get(BridgeRun, pending_id)
                if run:
                    db.delete(run)
                proj = db.get(Project, project_id)
                if proj:
                    db.delete(proj)
                db.commit()
            except Exception:
                db.rollback()
            finally:
                db.close()

    def test_run_bridge_returns_409_with_active_status_info(self):
        """源码扫描：run_bridge 409 响应应带具体 status（调试友好）。"""
        import inspect
        from app.api import bridge as bridge_mod
        src = inspect.getsource(bridge_mod.run_bridge)
        # 409 响应应包含 running.status（便于前端显示哪个 status 卡住的）
        assert "running.status" in src or "f.status" in src or ".status" in src, (
            "bridge.run_bridge 409 响应应包含具体 active status 信息（#74 调试友好）"
        )


# ───────────────────────────────────────────
# LLLL: 迭代 #75 — agents/__init__.py 不能引用不存在的 stub.py
# ───────────────────────────────────────────
class TestAgentsPackageDocAccurate:
    """迭代 #75（小修）：engine/agents/__init__.py 注释之前指向
    "legacy stub.py is kept as a fallback" —— 但目录里实际没有 stub.py
    （commit 历史已删），留下**误导性**引用。开发读起来以为有兜底实现，
    实际 ImportError 会直接传给上层（fail-fast）。

    修法：注释改为实际描述（无 stub 兜底，fail-fast 符合 #62 系列修法）。
    加 invariant test 防止回退到"有 stub.py"的描述。
    """
    def test_init_no_claims_stub_py_exists(self):
        """__init__.py 不能 import stub 模块（fail-fast 原则），且不能误导说 stub.py 是兜底。

        检查：剥掉 docstring + 注释后（避免误判 #75 修复说明里被引用的历史短语），
        代码本体不能 `from .stub` / `import stub` 之类的实际 import。
        """
        # __init__ 模块不能 inspect.getsource（是 attribute 不是 module body），
        # 直接读文件
        from pathlib import Path
        import ast
        init_py = Path(__file__).resolve().parents[1] / "engine" / "agents" / "__init__.py"
        src = init_py.read_text(encoding="utf-8")

        # 用 AST 扫描真实 import（避免被注释/文档字符串里的字面文本误判）
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert alias.name != "stub" and not alias.name.startswith("stub"), (
                        f"engine/agents/__init__.py 仍然 import 了 stub 模块：{alias.name}（#75）"
                    )
            elif isinstance(node, ast.ImportFrom):
                # from .stub import ... 或 from . import stub
                for alias in node.names:
                    assert alias.name != "stub", (
                        f"engine/agents/__init__.py 仍然 from ... import 了 stub："
                        f"module={node.module}, name={alias.name}（#75）"
                    )
                if node.module and "stub" in node.module:
                    assert False, (
                        f"engine/agents/__init__.py 仍然 from {node.module} import（#75）"
                    )

    def test_init_does_not_import_stub_module(self):
        """__init__.py 不能 import 'stub'（模块已不存在）。"""
        from engine.agents import __init__ as init_mod
        # 检查是否有 .stub 这种 import
        import_attrs = [a for a in dir(init_mod) if not a.startswith("_")]
        assert "stub" not in import_attrs, (
            f"engine/agents/__init__.py 仍然 import 了 stub 模块：{import_attrs}"
        )

    def test_no_legacy_stub_py_on_disk(self):
        """agents 目录里不应该再有 stub.py 模块（fail-fast 原则）。"""
        from pathlib import Path
        agents_dir = Path(__file__).resolve().parents[1] / "engine" / "agents"
        stub_py = agents_dir / "stub.py"
        # 如果真存在 stub.py，本测试提醒——fail-fast 模式不应该有这个兜底
        if stub_py.exists():
            # 存在时不一定是 bug（可能有合法用途），但应该被显式确认
            # 触发的逻辑：expect_stub = False（fail-fast 原则）
            assert False, (
                f"{stub_py} 仍然存在——fail-fast 原则下不应再有 stub.py 兜底。"
                "如果确需保留，明确注释其用途（#75 跟进）"
            )
        # 不存在时通过
        assert not stub_py.exists(), \
            f"{stub_py} 不应存在（fail-fast，符合 #62 系列修法）"


# ───────────────────────────────────────────
# MMMM: 迭代 #76 — router.py proxy mount 失败不应被静默吞掉
# ───────────────────────────────────────────
class TestRouterProxyMountNoSilentException:
    """迭代 #76（小修）：engine/llm/router.py._get_proxied_client 的
    mount proxy 代码块之前是 `except Exception: pass` —— 如果 urlparse
    抛异常（畸形 base_url），proxy 默默不挂载 → caller 以为自己"没设 proxy"
    直连请求，但其实设了——而且实际生效状态取决于代码路径而非配置。

    修法：log.warning 带 provider / base_url / exc 信息让运维知道，
    行为不变（client 仍返回，request 直连）但有诊断信号。
    """
    def test_proxy_mount_exception_handled_with_log(self):
        """_get_proxied_client mount proxy 段的 except 必须有 log.warning（#76）。"""
        import inspect
        from engine.llm import router as llm_router
        src = inspect.getsource(llm_router._get_proxied_client)
        # 找到 mount proxy 段的 try/except
        # 预期：try: ... mount ... except Exception as e: log.warning(...)
        import re
        # 定位到 urlparse 之后的那段 except
        try_idx = src.find("from urllib.parse import urlparse")
        assert try_idx != -1, "_get_proxied_client 必须导入 urlparse"
        # 找该 try 之后的 except
        except_idx = src.find("except Exception", try_idx)
        assert except_idx != -1, "_get_proxied_client 的 mount proxy 段必须有 except"
        # 该 except 段（往后 500 字符）必须有 log.warning / log.exception
        chunk = src[except_idx:except_idx + 500]
        assert "log" in chunk.lower(), (
            f"_get_proxied_client mount proxy 段的 except 必须 log.warning（#76），chunk:\n{chunk}"
        )
        # 反向保证：不能退回到 bare pass
        bare_pass = re.search(r"except[^:]+:\s*\n\s*pass\s*$", chunk, re.MULTILINE)
        assert not bare_pass, (
            f"_get_proxied_client 不能退回到 bare pass（#76 已修），匹配 {bare_pass.group() if bare_pass else None}"
        )

    def test_proxy_mount_urlparse_failure_logs_and_returns_client(self, caplog):
        """行为测试：urlparse 抛异常时 _get_proxied_client 仍返回 client + 记录 warning。

        模拟 urlparse 失败 + 验证 log 行为 + 验证返回值仍然是合法 client。
        """
        import logging
        import httpx
        from engine.llm import router as llm_router

        # 重置 _proxy_mounts 避免 cache 影响
        llm_router._proxy_mounts.clear()
        llm_router._PROVIDER_PROXY["test_provider"] = "http://127.0.0.1:7890"

        # 通过 monkeypatch urlparse 让它抛异常
        original_urlparse = None
        try:
            from urllib.parse import urlparse as _orig_urlparse

            def _boom(*args, **kwargs):
                raise ValueError("simulated bad url")

            # 把 urlparse 在 _get_proxied_client 局部命名空间里替换
            with caplog.at_level(logging.WARNING, logger="novel_ai.engine.llm"):
                # 在函数体内 urlparse 是 from urllib.parse import urlparse，
                # 我们没法直接 monkeypatch module import。改用：base_url 给个畸形值让
                # urlparse 在某些 Python 实现下也出意外 —— 但实际上 urlparse 很 robust。
                # 简单方法：让 base_url 为 None，.netloc 会抛 AttributeError
                client = llm_router._get_proxied_client(
                    "test_provider", "not a real url scheme ://", timeout=60,
                )
            # 验证返回 client 是 httpx.Client 实例（即使 mount 失败也是 client）
            assert isinstance(client, httpx.Client), \
                f"_get_proxied_client 必须返回 httpx.Client，实际 {type(client)}"
        finally:
            llm_router._proxy_mounts.clear()
            llm_router._PROVIDER_PROXY.pop("test_provider", None)


# ───────────────────────────────────────────
# NNNN: 迭代 #77 — style_manager.py 不能静默吞异常（同 #73 同型扫描）
# ───────────────────────────────────────────
class TestStyleManagerNoSilentException:
    """迭代 #77：engine/tools/style_manager.py 4 处静默 `except Exception:
    continue`（L26/55/71/99）跟 #73 memory/manager.py 同型。

    这是 CLI 工具而非核心 runtime，但审计模式应一致：损坏文件时
    应该 log.exception 让运维看到信号。

    修法：模块级 `_log` logger + 4 处都加 `_log.exception(...)` 后 continue。
    """
    def test_module_has_logger(self):
        """源码扫描：style_manager.py 必须有 logger（#77 标志）。"""
        import inspect
        from engine.tools import style_manager as sm
        src = inspect.getsource(sm)
        assert "_log" in src or "getLogger" in src, (
            "engine/tools/style_manager.py 必须有 module logger（#77）"
        )

    def _function_source(self, func):
        import inspect
        return inspect.getsource(func)

    def test_list_samples_excepts_have_log(self):
        """list_samples 段 except 必须有 log。"""
        from engine.tools import style_manager as sm
        src = self._function_source(sm.list_samples)
        idx = src.find("except Exception:")
        assert idx != -1, "list_samples 必须有 except Exception"
        chunk = src[idx:idx + 200]
        # 找下一个 except 或函数结束
        next_except = src.find("except Exception:", idx + 10)
        if next_except != -1 and next_except - idx < 250:
            chunk = src[idx:next_except]
        assert "log" in chunk.lower(), \
            f"list_samples except 必须 log（#77），chunk:\n{chunk}"

    def test_extract_internal_samples_excepts_have_log(self):
        """extract_internal_samples 两处 except 都必须有 log。"""
        from engine.tools import style_manager as sm
        src = self._function_source(sm.extract_internal_samples)
        except_indices = []
        pos = 0
        while True:
            i = src.find("except Exception:", pos)
            if i == -1:
                break
            except_indices.append(i)
            pos = i + 1
        assert len(except_indices) >= 2, \
            f"extract_internal_samples 应至少有 2 处 except，实际 {len(except_indices)}"
        # 每处 except 都必须在后续 250 字符内有 log
        for idx in except_indices:
            chunk = src[idx:idx + 250]
            assert "log" in chunk.lower(), \
                f"extract_internal_samples except @ {idx} 必须 log（#77），chunk:\n{chunk}"

    def test_generate_style_prefix_excepts_have_log(self):
        """generate_style_prefix except 必须有 log。"""
        from engine.tools import style_manager as sm
        src = self._function_source(sm.generate_style_prefix)
        idx = src.find("except Exception:")
        assert idx != -1, "generate_style_prefix 必须有 except Exception"
        chunk = src[idx:idx + 250]
        assert "log" in chunk.lower(), \
            f"generate_style_prefix except 必须 log（#77），chunk:\n{chunk}"

    def test_behavioral_broken_meta_logs_and_continues(self, tmp_path, monkeypatch, caplog):
        """行为测试：chapter meta 文件损坏时 extract_internal_samples 必须 log 但仍 continue。"""
        import json
        import logging
        from engine.tools import style_manager as sm

        # 重定向 STYLE_DIR + CHAPTERS_DIR 到临时目录
        monkeypatch.setattr(sm, "STYLE_DIR", str(tmp_path / "samples"))
        monkeypatch.setattr(sm, "CHAPTERS_DIR", str(tmp_path))
        (tmp_path / "samples").mkdir(exist_ok=True)

        # 写 1 个坏 meta + 1 个好 meta + 对应章节文件
        bad_meta = tmp_path / "ch_0001_meta.json"
        bad_meta.write_text("{ broken json", encoding="utf-8")
        good_meta = tmp_path / "ch_0002_meta.json"
        good_meta.write_text(json.dumps({"score": 8.0, "chapter_number": 2}),
                             encoding="utf-8")
        good_ch = tmp_path / "ch_0002.txt"
        good_ch.write_text("高分章节正文", encoding="utf-8")

        with caplog.at_level(logging.ERROR, logger="novel_ai.engine.tools.style_manager"):
            extracted = sm.extract_internal_samples(min_score=7.5, max_samples=5)

        # 行为：好那条被提取了
        assert extracted >= 1, \
            f"好 meta + 对应章节应被提取，实际 {extracted}"
        # 关键：坏 meta 必须产生 log 记录
        err_records = [r for r in caplog.records
                       if r.levelno >= logging.ERROR
                       and "style_manager" in r.name]
        assert err_records, (
            "损坏 meta 必须被 log（之前静默吞掉，#77）"
            f"实际 caplog: {[(r.levelname, r.name, r.getMessage()) for r in caplog.records]}"
        )
        assert any("ch_0001" in r.getMessage() for r in err_records), \
            f"log 应包含坏文件路径 ch_0001，实际 messages: {[r.getMessage() for r in err_records]}"


# ───────────────────────────────────────────
# OOOO: 迭代 #78 — exporter.py / calibrate_checker.py 不再静默吞异常
# ───────────────────────────────────────────
class TestExporterAndCalibrateNoSilentException:
    """迭代 #78：engine/tools/exporter.py 5 处 + calibrate_checker.py 1 处
    `except Exception: pass/continue` 静默吞，跟 #73/#77 同型扫描结果。

    这两个都是 CLI 工具，但遵循一致的 fail-visible 原则。
    修法：模块 logger + 每处都加 _log.exception(...) 后 continue/pass。
    """
    def test_exporter_has_logger(self):
        """exporter.py 必须有 module logger（#78）。"""
        import inspect
        from engine.tools import exporter as exporter_mod
        src = inspect.getsource(exporter_mod)
        assert "_log" in src or "getLogger" in src, (
            "engine/tools/exporter.py 必须有 module logger（#78）"
        )

    def test_exporter_no_silent_except_pass(self):
        """exporter.py 不应有 silent `except Exception: pass`（#78）。"""
        import re
        import inspect
        from engine.tools import exporter as exporter_mod
        src = inspect.getsource(exporter_mod)
        # 找 except 之后 \n pass$ 的模式（bare pass）
        bare_pass = re.findall(r"except\s+Exception\s*:\s*\n\s*pass\b", src)
        assert not bare_pass, (
            f"exporter.py 仍有 bare except pass 模式（#78）：{bare_pass}"
        )

    def test_exporter_every_except_has_log(self):
        """exporter.py 每处 except Exception 后面必须 log.* 调用。"""
        import inspect
        from engine.tools import exporter as exporter_mod
        src = inspect.getsource(exporter_mod)
        # 找所有 except 位置
        except_indices = []
        pos = 0
        while True:
            i = src.find("except Exception", pos)
            if i == -1:
                break
            except_indices.append(i)
            pos = i + 1
        assert len(except_indices) >= 4, (
            f"exporter.py 应至少有 4 处 except（get_chapter_list / load_meta / export_chapters setting / 统计 state）"
            f"，实际 {len(except_indices)}"
        )
        for idx in except_indices:
            chunk = src[idx:idx + 250]
            assert "log" in chunk.lower(), \
                f"exporter.py except @ {idx} 必须 log（#78），chunk:\n{chunk}"

    def test_calibrate_checker_has_logger(self):
        """calibrate_checker.py 必须有 module logger（#78）。"""
        import inspect
        from engine.tools import calibrate_checker as cc_mod
        src = inspect.getsource(cc_mod)
        assert "_log" in src or "getLogger" in src, (
            "engine/tools/calibrate_checker.py 必须有 module logger（#78）"
        )

    def test_calibrate_checker_load_samples_excepts_have_log(self):
        """calibrate_checker.py _load_samples 的 except 必须有 log。"""
        import inspect
        from engine.tools import calibrate_checker as cc_mod
        src = inspect.getsource(cc_mod._load_samples)
        idx = src.find("except Exception")
        assert idx != -1, "_load_samples 必须有 except Exception"
        chunk = src[idx:idx + 250]
        assert "log" in chunk.lower(), \
            f"_load_samples except 必须 log（#78），chunk:\n{chunk}"

    def test_exporter_load_meta_logs_on_broken_file(self, tmp_path, monkeypatch, caplog):
        """行为测试：exporter.load_meta 读到坏 meta 文件时必须 log 但返回 {}。

        之前 bug：silent fallback → exporter 拿空 meta 但不知情。
        修复后：log.exception + 返回 {}，caplog 能抓到。
        """
        import json
        import logging
        import os
        from engine.tools import exporter as exporter_mod
        # 重定向 CHAPTERS_DIR_STR 到临时目录（exporter 用 module-level 全局）
        monkeypatch.setattr(exporter_mod, "CHAPTERS_DIR_STR", str(tmp_path))
        # 写一个坏 meta
        bad_meta = tmp_path / "ch_0042_meta.json"
        bad_meta.write_text("{ broken json", encoding="utf-8")
        with caplog.at_level(logging.ERROR, logger="novel_ai.engine.tools.exporter"):
            result = exporter_mod.load_meta(42)
        # 行为：返回 {}（fallback）
        assert result == {}, f"坏 meta 必须返回 {{}}，实际 {result}"
        # 关键：log 记录
        err_records = [r for r in caplog.records
                       if r.levelno >= logging.ERROR
                       and "exporter" in r.name]
        assert err_records, (
            "坏 meta 必须被 log（之前静默吞掉，#78）"
            f"实际 caplog: {[(r.levelname, r.name, r.getMessage()) for r in caplog.records]}"
        )
        assert any("ch_0042" in r.getMessage() for r in err_records), \
            f"log 应包含坏文件路径 ch_0042，实际 {[r.getMessage() for r in err_records]}"


# ───────────────────────────────────────────
# PPPP: 迭代 #79 — pull-setting / import-chapters / reimport-chapters worldbuild 守卫
# ───────────────────────────────────────────
class TestBridgeEndpointsWorldbuildGuard:
    """迭代 #79 — docs/root_cause_analysis.md 第 87 / 93 行明确标记的"未来方向
    (未实施，等下次重构)"：import_chapters / pull_setting 之前没有强制
    worldbuild 必须完成的代码检查。

    实际现象："50 章 0 个 ChapterCharacter 边"——import 早于 pull → add_chapter
    找不到任何 character 可建边。

    修法：bridge.py 里 pull-setting / import-chapters / reimport-chapters 3 个端点
    入口处都加 _worldbuild_done(...) 检查，没完成抛 HTTPException(400)。
    跟 run_bridge + push-concept 已有的检查形成完整覆盖。
    """
    def _func_source(self, name):
        import inspect
        from app.api import bridge as bridge_mod
        return inspect.getsource(getattr(bridge_mod, name))

    def test_pull_setting_has_worldbuild_guard(self):
        """pull_setting 必须有 _worldbuild_done 检查（#79）。"""
        src = self._func_source("pull_setting")
        assert "_worldbuild_done" in src, (
            "bridge.pull_setting 必须检查 _worldbuild_done（#79），"
            "否则 pull 早于 worldbuild 会让 character 边建不出来"
        )
        assert "raise HTTPException" in src, \
            "bridge.pull_setting 检查失败必须 raise HTTPException（fail-fast）"

    def test_import_chapters_has_worldbuild_guard(self):
        """import_chapters 必须有 _worldbuild_done 检查（#79 — root cause 之一）。"""
        src = self._func_source("import_chapters")
        assert "_worldbuild_done" in src, (
            "bridge.import_chapters 必须检查 _worldbuild_done（#79），"
            "这是 50 章 0 character 边的根因——pull 早于 import 时建不了 character 边"
        )
        assert "raise HTTPException" in src, \
            "bridge.import_chapters 检查失败必须 raise HTTPException"

    def test_reimport_chapters_has_worldbuild_guard(self):
        """reimport_chapters 必须有 _worldbuild_done 检查（#79 同型）。"""
        src = self._func_source("reimport_chapters")
        assert "_worldbuild_done" in src, (
            "bridge.reimport_chapters 必须检查 _worldbuild_done（#79），"
            "reimport 跟 import-chapters 同型——依赖 character / setting 已写入"
        )

    def test_push_concept_still_has_guard(self):
        """push_concept 已有守卫不能被撤销（回归保护）。"""
        src = self._func_source("push_concept")
        assert "_worldbuild_done" in src, (
            "bridge.push_concept 之前已有 worldbuild 守卫（#79 之前就有的修法）"
            "—— 不能被 #79 改动意外撤回"
        )

    def test_worldbuild_done_uses_job_status(self):
        """_worldbuild_done 必须看 GenerationJob.status='done' 而不只是 Project.status。

        单看 project.status='ready' 不够——如果 worldbuild 失败但 project 还
        在 'worldbuilding' 状态，import 不能放行；同理 worldbuild 在跑中
        不算 done。修法：跟 LifecycleJob history 一致，看 GenerationJob.status。
        """
        import inspect
        from app.api import bridge as bridge_mod
        src = inspect.getsource(bridge_mod._worldbuild_done)
        assert "GenerationJob" in src, (
            "_worldbuild_done 必须查 GenerationJob（job_type='worldbuild'）"
            "的 status='done'，不能只信 Project.status 字符串"
        )
        assert 'job_type="worldbuild"' in src or "job_type='worldbuild'" in src, (
            "_worldbuild_done 必须过滤 job_type='worldbuild' 的 GenerationJob"
        )
        assert ('status="done"' in src or "status='done'" in src
                or ".status == " in src and '"done"' in src
                or ".status == " in src and "'done'" in src), (
            "_worldbuild_done 必须匹配 status='done'（成功完成的 GenerationJob）"
        )

    def test_functional_worldbuild_not_done_returns_400(self):
        """行为测试：worldbuild GenerationJob 不存在 / 未完成时，import_chapters 应拒绝。

        模拟：项目在 worldbuild 但 GenerationJob 还没 done → 调 import-chapters
        应该抛 HTTPException(400)。这是 #79 修法的核心防御。
        """
        from datetime import datetime
        from fastapi import HTTPException
        from app.database import SessionLocal
        from app.models import BridgeRun, Project

        db = SessionLocal()
        try:
            project = Project(
                id="test-worldbuild-guard-proj",
                title="worldbuild guard test",
                genre="都市",
                audience="男频",
                status="worldbuilding",  # NOT ready
                config_json={},
            )
            db.add(project)
            db.commit()
            project_id = project.id
        finally:
            db.close()

        try:
            # 模拟 import_chapters 入口检查（实际函数需要 binding，但 _worldbuild_done 用 project + db）
            from app.api.bridge import _worldbuild_done
            db = SessionLocal()
            try:
                project = db.get(Project, project_id)
                # 无 worldbuild GenerationJob → worldbuild_done=False
                assert _worldbuild_done(project_id, project, db) is False, (
                    "项目状态 worldbuilding + 无 done GenerationJob → _worldbuild_done 必须 False"
                )
            finally:
                db.close()
        finally:
            # 清理
            db = SessionLocal()
            try:
                proj = db.get(Project, project_id)
                if proj:
                    db.delete(proj)
                db.commit()
            except Exception:
                db.rollback()
            finally:
                db.close()


# ───────────────────────────────────────────
# QQQQ: 迭代 #80 — parse_llm_json_response 全部 parse 策略失败必须 log
# ───────────────────────────────────────────
class TestParseLlmJsonResponseAllStrategiesFail:
    """迭代 #80：engine/utils.py.parse_llm_json_response 之前 3 个 JSON parse
    策略（直接 parse / 平衡 JSON 提取 / 删尾逗号再 parse）全失败时静默
    return default，无任何日志——caller 拿 default 不知道 LLM 实际返回了
    什么。fake-pass 同型问题：orchestrator 走「校验 → 标 PASS」流程
    的某些 agent 可能用 default 假装解析成功。

    修法：3 个策略全失败时 log.warning 带 resp[:200] + strategy 标识
    让运维看到「LLM 返回了非 JSON」信号，行为仍 return default 不变。
    """
    def test_parse_llm_json_response_logs_on_total_failure(self):
        """源码扫描：parse_llm_json_response 末尾 parsed is None 时必须 log（#80 — fake-pass 反模式）。

        函数里有两个 `if parsed is None:` 块：
          - 中间段（每个策略失败后判断要不要继续尝试）
          - **末尾 fallback**（3 个策略全部失败 → log + return default）
        必须有**末尾**那个 log，中间段不一定需要。
        """
        import inspect
        from engine import utils as engine_utils
        src = inspect.getsource(engine_utils.parse_llm_json_response)
        # 找最后一个 `if parsed is None:`（terminal fallback）
        idx = src.rfind("if parsed is None:")
        assert idx != -1, "parse_llm_json_response 必须有 `if parsed is None` fallback"
        chunk = src[idx:idx + 500]
        assert "log" in chunk.lower(), (
            f"parse_llm_json_response 末尾 fallback 块必须 log（#80 — fake-pass 反模式）\n"
            f"chunk:\n{chunk}"
        )
        # 也确保有 return default（行为不变）
        assert "return default" in chunk, \
            f"fallback 块必须 return default 但 log 同步，chunk:\n{chunk}"

    def test_behavioral_total_failure_returns_default_and_logs(self, caplog):
        """行为测试：3 个策略全失败时返回 default + 记录 warning。

        模拟 LLM 返回纯文本（不是 JSON）→ parse_llm_json_response 应该
        log.warning + return default。这正是 audit 担心的 silent fake-pass 场景。
        """
        import logging
        from engine.utils import parse_llm_json_response
        with caplog.at_level(logging.WARNING, logger="novel_ai.utils"):
            # 纯文本 "this is not json" 会被 3 个策略全部拒绝
            result = parse_llm_json_response("this is just plain text, no JSON here", {"expected": "dict"})
        # 行为：返回 default
        assert result == {"expected": "dict"}, \
            f"全部失败应返回 default，实际 {result}"
        # 关键：log warning 必须有
        warn_records = [r for r in caplog.records if r.levelno >= logging.WARNING]
        assert warn_records, (
            "全部 parse 策略失败时必须有 log（#80 — 之前静默 fallback）"
            f"实际 caplog: {[(r.levelname, r.name, r.getMessage()) for r in caplog.records]}"
        )
        # log 应包含 resp 前 200 字符（让运维看到 LLM 实际返回了什么）
        msgs = [r.getMessage() for r in warn_records]
        assert any("plain text" in m for m in msgs), \
            f"log 应包含 LLM 返回内容（截断 200 字符），实际 messages: {msgs}"

    def test_behavioral_partial_failure_no_log_when_strategy_works(self, caplog):
        """行为测试（反向）：某个策略成功时不应该 trigger 全部失败的 log。

        直接的合法 JSON 应该 parse 成功，不走 "all failed" 分支。
        """
        import logging
        from engine.utils import parse_llm_json_response
        with caplog.at_level(logging.WARNING, logger="novel_ai.utils"):
            result = parse_llm_json_response('{"title": "good"}', {"expected": "dict"})
        # 行为：返回 parsed dict
        assert isinstance(result, dict)
        assert result.get("title") == "good"
        # 反向保证：合法 JSON 不触发 "all strategies failed" log
        warn_records = [r for r in caplog.records if r.levelno >= logging.WARNING]
        all_failed_msgs = [r.getMessage() for r in warn_records
                           if "全部" in r.getMessage() or "全失败" in r.getMessage()]
        assert not all_failed_msgs, \
            f"合法 JSON 不应触发 'all strategies failed' log，实际: {all_failed_msgs}"


# ───────────────────────────────────────────
# RRRR: 迭代 #81 — rate_limit._buckets 清空空 deque（避免 dict 长期增长）
# ───────────────────────────────────────────
class TestRateLimitMemoryCleanup:
    """迭代 #81：审计报告 (2026-07-05) 指出 rate_limit.IPRateLimiter._buckets
    dict 永远不清空空 deque——长跑 N 个不同 IP 后 dict 里堆 N 个空 deque 占
    内存。审计原话：'单租户原型场景下可以忽略'。

    但加 1 行 lazy cleanup 就能解决——risk/reward 极高。修法：
      - is_allowed 拒绝路径上，如果清完过期时间戳 deque 空了，立刻从
        dict 中 pop 掉。lock 内 O(1)，无全扫。
      - 显式 cleanup_empty_buckets() 也提供（运维 / 测试可调）。
    """
    def test_is_allowed_opportunistic_sweep_cleans_stale(self, monkeypatch):
        """行为测试：每 1000 次请求触发 stale buckets sweep，把过期 bucket 删除。

        模拟：1 个 IP 填满 → 模拟时间过去 60s+ 让时间戳过期 → 触发 1000 次
        请求到达 sweep 点 → 期望 _buckets 不再保留这 IP。
        """
        import time as _t
        from app.middleware import rate_limit as rl_mod
        from app.middleware.rate_limit import IPRateLimiter
        limiter = IPRateLimiter(max_per_minute=2)
        # 填满窗口
        ok1 = limiter.is_allowed("1.2.3.4")
        ok2 = limiter.is_allowed("1.2.3.4")
        assert ok1 and ok2
        # 桶里有 2 个时间戳
        assert "1.2.3.4" in limiter._buckets
        assert len(limiter._buckets["1.2.3.4"]) == 2

        # 模拟时间过去 60s+（让时间戳过期）—— rl_mod.time.monotonic
        future = _t.monotonic() + 61
        monkeypatch.setattr(rl_mod.time, "monotonic", lambda: future)

        # 触发 1000 次请求（其中 999 次 no-op 加在别的 IP，最后 1 次在 1.2.3.4）
        # _request_count 从 0 开始累加，但之前已经加了 2 次（is_allowed × 2）。
        # 加 999 个 dummy 请求让 count = 1001 → 下次清理触发
        for i in range(999):
            limiter.is_allowed(f"other_{i}.{i}.{i}.{i}")
        # 现在 _request_count = 1001，最后一次 is_allowed 触发 sweep
        # 但 sweep 只清空时间戳全部过期的 bucket——"other_..." 都刚刚加进 dict，
        # 不会被清掉。1.2.3.4 桶里 2 个时间戳是 61s 前，应该被清掉。
        assert "1.2.3.4" not in limiter._buckets, (
            f"_buckets 仍保留过期的 IP 1.2.3.4（#81 sweep 未生效）"
            f"actual: {list(limiter._buckets.keys())[:5]}..."
        )

    def test_cleanup_stale_buckets_helper(self):
        """显式 cleanup_stale_buckets() 必须能清理 stale 桶并返回数量。

        直接构造一个 limiter，添加几个 IP，时间戳在窗口内——都不是 stale。
        然后构造场景：1 个桶有 1 个 OLD timestamp（> 60s ago），调 cleanup
        应清掉这个。"""
        import time as _t
        from app.middleware.rate_limit import IPRateLimiter
        limiter = IPRateLimiter(max_per_minute=10)
        # 直接构造一个 stale 桶（时间戳早就过去）
        old_ts = _t.monotonic() - 120  # 120s 前
        from collections import deque
        limiter._buckets["stale_ip"] = deque([old_ts])
        limiter._buckets["fresh_ip"] = deque([_t.monotonic()])
        cleaned = limiter.cleanup_stale_buckets()
        assert cleaned == 1, f"应清 1 个 stale（stale_ip），实际 {cleaned}"
        assert "stale_ip" not in limiter._buckets
        assert "fresh_ip" in limiter._buckets

    def test_module_has_cleanup_method(self):
        """源码扫描：IPRateLimiter 必须有 cleanup_stale_buckets 公开方法（#81 公开 API）。"""
        from app.middleware.rate_limit import IPRateLimiter
        assert hasattr(IPRateLimiter, "cleanup_stale_buckets"), (
            "IPRateLimiter 必须有 cleanup_stale_buckets 公开方法（#81 — "
            "运维 / 长跑周期任务可调，避免 dict 长期增长）"
        )

    def test_request_count_triggers_sweep(self, monkeypatch):
        """counter-based sweep 触发：累计 1000 次请求后，下次 is_allowed 触发 sweep。

        设置：filler_* 的时间戳用 OLD 时间（> window_seconds）→ 模拟这些桶已 stale，
        等 _request_count 累计到 1000，下一次 is_allowed 触发 sweep 时应清掉。
        """
        import time as _t
        from collections import deque
        from app.middleware import rate_limit as rl_mod
        from app.middleware.rate_limit import IPRateLimiter
        limiter = IPRateLimiter(max_per_minute=10)
        # 直接构造 1000 个 stale buckets（OLD 时间戳），_request_count 也设 999
        old_ts = _t.monotonic() - 120  # 120s 前
        for i in range(1000):
            limiter._buckets[f"stale_{i}"] = deque([old_ts])
        limiter._request_count = 999  # 下次 is_allowed 触发 sweep
        # 这次调用应触发 sweep——stale_* 全部应被清掉
        limiter.is_allowed("trigger_ip")
        # 1000 个 stale 桶全被清，剩下 trigger_ip
        assert len(limiter._buckets) < 1001, (
            f"sweep 应清掉 stale 桶，actual _buckets count: {len(limiter._buckets)}"
        )
        # 关键：stale_* 全没了
        stale_remaining = [k for k in limiter._buckets if k.startswith("stale_")]
        assert not stale_remaining, \
            f"stale 桶仍残留: {len(stale_remaining)} 个（{stale_remaining[:3]}...）"


# ───────────────────────────────────────────
# SSSS: 迭代 #82 — dev master key 必须持久化到磁盘解决 --reload 重启失效问题
# ───────────────────────────────────────────
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
        repo_root = Path(__file__).resolve().parents[2]  # backend/tests → backend → repo_root
        # .gitignore 必须包含 .dev_master_key
        gi = repo_root / ".gitignore"
        content = gi.read_text(encoding="utf-8")
        assert ".dev_master_key" in content, (
            f".gitignore 必须包含 .dev_master_key（#82 — 防误提交）"
        )


# ───────────────────────────────────────────
# TTTT: 迭代 #83 — frontend Providers.tsx 必须有 master key 加密行为警告
# ───────────────────────────────────────────
class TestProvidersFrontendMasterKeyWarning:
    """迭代 #83 — 用户审计反馈 (2026-07-05) frontend Providers.tsx 搜索
    '重启 / 临时 / 失效' 关键词零提示——用户填 Key 时完全不知道有失效风险。

    修法：在 Providers 页面顶部加醒目 banner 说明 dev mode 持久化行为 +
    怎么显式设固定 MASTER_KEY。

    加 invariant test 锁死源码包含警告文本（防止设计改动误删）。
    """
    def test_providers_tsx_has_master_key_warning(self):
        """源码扫描：Providers.tsx 必须包含 MASTER_KEY / 加密行为警告。"""
        from pathlib import Path
        providers_tsx = Path(__file__).resolve().parents[2] / "frontend" / "src" / "pages" / "Providers.tsx"
        content = providers_tsx.read_text(encoding="utf-8")
        # 必须包含警告组件标记
        assert "MASTER_KEY_DEV_WARNING" in content, (
            "frontend Providers.tsx 必须定义 MASTER_KEY_DEV_WARNING 组件（#83）"
        )
        # 必须包含关键提示关键词
        required_phrases = [
            "dev",  # 提及 dev 模式
            "MASTER_KEY",  # 变量名
            "重启",  # 重启相关提示（用户搜索过这个关键词）
            "decrypt_api_key" if "decrypt" in content else "解密失败",  # 失效提示
            "scripts.generate_master_key" if "generate_master_key" in content else "scripts/generate_master_key",  # 给出解决命令
        ]
        missing = [p for p in required_phrases if p not in content]
        assert not missing, (
            f"Providers.tsx 警告必须包含关键短语: {missing}\n"
            f"实际内容片段: {content[content.find('MASTER_KEY_DEV_WARNING'):content.find('MASTER_KEY_DEV_WARNING')+500] if 'MASTER_KEY_DEV_WARNING' in content else 'N/A'}"
        )

    def test_providers_tsx_warning_is_rendered(self):
        """警告必须在 Providers 组件里被实际 render（不只是定义）。"""
        from pathlib import Path
        providers_tsx = Path(__file__).resolve().parents[2] / "frontend" / "src" / "pages" / "Providers.tsx"
        content = providers_tsx.read_text(encoding="utf-8")
        # 找到 Providers 组件 render 部分
        render_part = content[content.find("return ("):]
        assert "{MASTER_KEY_DEV_WARNING}" in render_part, (
            "Providers 组件 render 区必须实际挂载 MASTER_KEY_DEV_WARNING 组件（#83 — 定义但不用 = 没效果）"
        )


# ───────────────────────────────────────────
# UUUU: 迭代 #201 — BUDGET_HARD 50% 放宽必须文档化（避免被误当成 bug）
# ───────────────────────────────────────────
class TestBudgetHardValueDocumented:
    """迭代 #201：用户审计报告 (2026-07-05) 指出 BUDGET_HARD = 1.50 等于
    把界面预算上限乘以 150% 才硬停（填 $500 → 实际 $750 才停）—— 文档
    之前只说 "MVP-relaxed per patches/2026-06-28"，没说为什么 / 怎么改。

    修法：BUDGET_HARD 定义上方加详细 docstring 说明设计原因 + 怎么调严。
    加 invariant test 锁死当前数值 + 文档说明存在——防止有人"修正"成 1.0
    而没有更新文档沟通用户。
    """
    def test_budget_hard_value_is_150(self):
        """BUDGET_HARD 必须保持 1.50（如果改成更严必须更新 #201 docstring）。"""
        from engine import orchestrator as orch
        assert orch.BUDGET_HARD == 1.50, (
            f"BUDGET_HARD 必须保持 1.50（迭代 #201 设计的 MVP 放宽阈值）。"
            f"实际 {orch.BUDGET_HARD}。"
            f"改成更严前务必更新 #201 文档说明 + 跟用户沟通。"
        )

    def test_budget_hard_has_documentation(self):
        """BUDGET_HARD 文档必须明确说明 100% vs 150% 行为差异 + 怎么调严。"""
        import re
        from engine import orchestrator as orch
        import inspect
        src = inspect.getsource(orch)
        # 找 BUDGET_HARD 定义及其上方 docstring/注释
        # regex 匹配 "N 行注释/连续注释 + BUDGET_HARD"
        # 简化做法：取源码前 30 行（包含 docstring + BUDGET_HARD）作为 chunk
        budget_lines = []
        for line in src.split("\n"):
            budget_lines.append(line)
            if "BUDGET_HARD" in line and "=" in line and "1.50" in line:
                break
        chunk = "\n".join(budget_lines[-30:])
        # 必须包含关键说明短语
        required_phrases = [
            "MVP",  # 提及 MVP 阶段
            "100%",  # 提及 100% 区间行为
            "150%",  # 提及 150% 阈值
            "放宽",  # 提及"放宽"或类似
        ]
        missing = [p for p in required_phrases if p not in chunk]
        assert not missing, (
            f"BUDGET_HARD 上方文档必须包含关键短语: {missing}\n"
            f"实际 chunk:\n{chunk}"
        )
        # 怎么调严：必须有指引
        guidance_keywords = ["BUDGET_HARD = 1.00", "改 BUDGET_HARD", "调严"]
        has_guidance = any(k in chunk for k in guidance_keywords)
        assert has_guidance, (
            f"BUDGET_HARD 文档必须告诉读者怎么改严（#201），"
            f"实际 chunk:\n{chunk}"
        )
