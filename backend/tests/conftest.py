"""backend/tests/conftest.py — pytest 共享配置

Phase D 修复：让 pytest 从任何 cwd 都能正确收集 backend/tests/ 下的测试。

核心问题：tests/invariants/test_X.py 子包用 `from tests.X import ...`
相对导入，需要 backend/ 在 sys.path。但老 conftest 不存在，pytest
自动发现无法保证 backend/ 在 sys.path 里（取决于 invocation cwd）。

修法：在 backend/tests/ 下放 conftest.py，pytest 收集时自动执行：
  1. 把 backend/ 插入 sys.path（解决 tests.X 相对导入）
  2. 暴露 REPO_ROOT / BACKEND_ROOT 给 fixture 路径测试使用
"""
from __future__ import annotations

import sys
from pathlib import Path

# 把 backend/ 插入 sys.path（让 tests.invariants 等子包可被 import）
_BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))