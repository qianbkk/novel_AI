"""novel_AI engine config package.

Externalized prompt templates + power-level + path constants.
Mirrors the novel_AI/config/* layout for 1:1 prompt migration.
"""
from .prompt_templates import (
    HOOK_TYPES, SHUANG_TYPES, GENRE_WRITING_INSTRUCTIONS,
    UNIVERSAL_WRITING_RULES, CHARACTER_VOICE_REMINDER_TEMPLATE,
    get_character_voice_reminder, get_hook_guidance, get_genre_instruction,
)
from .power_levels import POWER_LEVELS, DEFAULT_POWER_LEVEL, POWER_LEVEL_ORDER
from .paths import (
    BACKEND_DIR, DATA_DIR, OUTPUT_DIR, CHAPTERS_DIR, STATE_PATH, SETTING_PATH,
    L2_DIR, L5_DIR, STYLE_SAMPLES_DIR, CONFIG_DIR,
)

__all__ = [
    "HOOK_TYPES", "SHUANG_TYPES", "GENRE_WRITING_INSTRUCTIONS",
    "UNIVERSAL_WRITING_RULES", "CHARACTER_VOICE_REMINDER_TEMPLATE",
    "get_character_voice_reminder", "get_hook_guidance", "get_genre_instruction",
    "POWER_LEVELS", "DEFAULT_POWER_LEVEL", "POWER_LEVEL_ORDER",
    "BACKEND_DIR", "DATA_DIR", "OUTPUT_DIR", "CHAPTERS_DIR",
    "STATE_PATH", "SETTING_PATH", "L2_DIR", "L5_DIR",
    "STYLE_SAMPLES_DIR", "CONFIG_DIR",
]