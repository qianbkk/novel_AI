"""Path constants shared across all modules."""
import os

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # = novel_AI/
OUTPUT_DIR = os.path.join(BASE_DIR, "output")
CHAPTERS_DIR = os.path.join(OUTPUT_DIR, "chapters")
STATE_PATH = os.path.join(OUTPUT_DIR, "orchestrator_state.json")
SETTING_PATH = os.path.join(OUTPUT_DIR, "setting_package.json")
L2_DIR = os.path.join(BASE_DIR, "memory", "l2")
L5_DIR = os.path.join(BASE_DIR, "memory", "l5")
STYLE_SAMPLES_DIR = os.path.join(OUTPUT_DIR, "style_samples")

os.makedirs(OUTPUT_DIR, exist_ok=True)
os.makedirs(CHAPTERS_DIR, exist_ok=True)
os.makedirs(L2_DIR, exist_ok=True)
os.makedirs(L5_DIR, exist_ok=True)
os.makedirs(STYLE_SAMPLES_DIR, exist_ok=True)