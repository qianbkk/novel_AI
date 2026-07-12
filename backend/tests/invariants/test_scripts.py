"""scripts/ — Phase 3 测试拆分

不变量测试按业务域分文件存放。
原文件位置：tests/test_invariants.py（已替换为 re-export shim）
"""

from tests._paths import REPO_ROOT, BACKEND_ROOT
import json
import sys
from pathlib import Path
import pytest

BACKEND = Path(REPO_ROOT)
sys.path.insert(0, str(BACKEND))

# ── 原 test_invariants.py 顶部声明的 app.schema_validator 系列 ──
from app.schema_validator import (  # noqa: E402,F401
    validate_setting_package, validate_chapter_meta, SchemaError,
    get_setting_package_schema, get_chapter_meta_schema,
    validate_world_view_rich, validate_character_card, validate_entity_relation_rich,
    get_world_view_rich_schema, get_character_card_schema, get_entity_relation_rich_schema,
)

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
