"""Path constants shared across engine modules.

All paths are derived from backend/ as the root. Engine data files
(chapters / state / setting / memory / style samples) live under
backend/data/engine/, layout centralized here so all modules import
from a single source of truth.

修订 2026-07-16：删除 novel_AI/ 之后，本文件不再需要「mirror novel_AI
layout」的注释——backend 路径就是唯一的真实落盘位置。
"""
from __future__ import annotations
import os
from pathlib import Path

# ── Root paths ──
ENGINE_DIR  = Path(__file__).resolve().parent.parent          # backend/engine/
BACKEND_DIR = ENGINE_DIR.parent                                # backend/
DATA_DIR    = BACKEND_DIR / "data"

# ── Engine data layout ──
ENGINE_DATA_DIR    = DATA_DIR / "engine"
OUTPUT_DIR         = ENGINE_DATA_DIR / "output"
CHAPTERS_DIR       = OUTPUT_DIR / "chapters"
STATE_PATH         = OUTPUT_DIR / "orchestrator_state.json"
SETTING_PATH       = OUTPUT_DIR / "setting_package.json"
CONFIG_DIR         = ENGINE_DATA_DIR / "config"
NOVEL_CONFIG_PATH  = CONFIG_DIR / "novel_config.json"

# ── Memory layout ──
MEMORY_DIR = ENGINE_DATA_DIR / "memory"
L2_DIR     = MEMORY_DIR / "l2"
L5_DIR     = MEMORY_DIR / "l5"

# ── Style samples ──
STYLE_SAMPLES_DIR = OUTPUT_DIR / "style_samples"

# ── Compliance rule files ──
COMPLIANCE_RULES_DIR = ENGINE_DIR / "config" / "compliance_rules"

# ── Make sure all dirs exist ──
for d in (DATA_DIR, ENGINE_DATA_DIR, OUTPUT_DIR, CHAPTERS_DIR,
          CONFIG_DIR, MEMORY_DIR, L2_DIR, L5_DIR, STYLE_SAMPLES_DIR,
          COMPLIANCE_RULES_DIR):
    d.mkdir(parents=True, exist_ok=True)


def novel_config_path() -> Path:
    """novel_config.json 的运行时落盘位置（env-aware）。

    push-concept 写到 binding.novel_ai_dir/config/novel_config.json，
    引擎子进程经 NOVEL_AI_DIR env 拿到同一目录；读取端必须与写入端一致，
    否则绑定非默认目录的项目会读到固定路径下残留的旧配置（跨项目串味）。
    与 graph._engine_output_dir 同理：每次调用重读 os.environ，
    不用 import-time 缓存（测试在 import 后改 env 时缓存会过时）。
    """
    val = os.environ.get("NOVEL_AI_DIR")
    if val:
        return Path(val) / "config" / "novel_config.json"
    return NOVEL_CONFIG_PATH


# Backward-compat string aliases (the old api_client.py and orchestrator
# used string paths in some places).
def _as_str(p: Path) -> str:
    return str(p)


BACKEND_DIR_STR = _as_str(BACKEND_DIR)
OUTPUT_DIR_STR  = _as_str(OUTPUT_DIR)
CHAPTERS_DIR_STR = _as_str(CHAPTERS_DIR)
STATE_PATH_STR  = _as_str(STATE_PATH)
SETTING_PATH_STR = _as_str(SETTING_PATH)
L2_DIR_STR      = _as_str(L2_DIR)
L5_DIR_STR      = _as_str(L5_DIR)
STYLE_SAMPLES_DIR_STR = _as_str(STYLE_SAMPLES_DIR)
COMPLIANCE_RULES_DIR_STR = _as_str(COMPLIANCE_RULES_DIR)