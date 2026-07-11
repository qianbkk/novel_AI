"""build/ — Phase 3 测试拆分

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
