"""Power level definitions shared across agents and tools."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Level names and thresholds (threshold, level_number)
POWER_LEVELS = {
    "感债者": (1, 0),
    "识债者": (2, 500),
    "接债者": (3, 2000),
    "理债者": (4, 8000),
    "断债者": (5, 30000),
    "债主": (6, 100000),
}

DEFAULT_POWER_LEVEL = "感债者"

# Level names as ordered list for consistency
POWER_LEVEL_ORDER = ["感债者", "识债者", "接债者", "理债者", "断债者", "债主"]