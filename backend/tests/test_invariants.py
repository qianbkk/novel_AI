"""test_invariants.py — Phase 3 子包 re-export shim

原 8500 行单文件已按业务域拆分到 invariants/
本文件保留作为向后兼容入口（pytest discoverable 会先收这一个）—
实际测试现在跑在子包文件里。

为什么留这个 shim 而不直接删：
  - 外部脚本 / CI 命令 `pytest tests/test_invariants.py` 仍可工作
  - git log 检索 `tests/test_invariants.py::` 不全断
  - `pytest tests/` 默认全部 collect 一次，子包会自动被收
"""

from tests.invariants.test_audit import *  # noqa: F401,F403  # Phase 3 split
from tests.invariants.test_backup import *  # noqa: F401,F403  # Phase 3 split
from tests.invariants.test_bridge import *  # noqa: F401,F403  # Phase 3 split
from tests.invariants.test_build import *  # noqa: F401,F403  # Phase 3 split
from tests.invariants.test_deploy import *  # noqa: F401,F403  # Phase 3 split
from tests.invariants.test_engine import *  # noqa: F401,F403  # Phase 3 split
from tests.invariants.test_frontend_align import *  # noqa: F401,F403  # Phase 3 split
from tests.invariants.test_misc import *  # noqa: F401,F403  # Phase 3 split
from tests.invariants.test_mock_provider import *  # noqa: F401,F403  # Phase 3 split
from tests.invariants.test_rate_limit import *  # noqa: F401,F403  # Phase 3 split
from tests.invariants.test_schemas import *  # noqa: F401,F403  # Phase 3 split
from tests.invariants.test_scripts import *  # noqa: F401,F403  # Phase 3 split
from tests.invariants.test_security import *  # noqa: F401,F403  # Phase 3 split
from tests.invariants.test_worldbuild import *  # noqa: F401,F403  # Phase 3 split
