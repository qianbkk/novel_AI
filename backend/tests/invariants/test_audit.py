"""audit/ — Phase 3 测试拆分

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
