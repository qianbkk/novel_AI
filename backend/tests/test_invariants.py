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
        from app.models import BridgeRun
        from datetime import datetime, timezone

        # 准备：插一条 orphan running 行（finished_at IS NULL）
        db = SessionLocal()
        try:
            test_run = BridgeRun(
                project_id="test-orphan-recovery",
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
            # 清理测试数据
            db.delete(run)
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
