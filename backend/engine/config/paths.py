"""Path constants shared across engine modules.

All paths are derived from backend/ as the root, mirroring the original
novel_AI/ layout so data files written by the new engine land in the
same logical places (backend/data/engine/{output,memory,style_samples}).
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

# ── Compliance rule files (mirrors novel_AI/config/compliance_rules_*.json) ──
COMPLIANCE_RULES_DIR = ENGINE_DIR / "config" / "compliance_rules"

# ── Make sure all dirs exist ──
for d in (DATA_DIR, ENGINE_DATA_DIR, OUTPUT_DIR, CHAPTERS_DIR,
          CONFIG_DIR, MEMORY_DIR, L2_DIR, L5_DIR, STYLE_SAMPLES_DIR,
          COMPLIANCE_RULES_DIR):
    d.mkdir(parents=True, exist_ok=True)


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