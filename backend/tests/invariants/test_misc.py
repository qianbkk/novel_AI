"""misc/ — Phase 3 测试拆分

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
