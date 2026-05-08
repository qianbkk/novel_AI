"""Shared utility functions for LLM response parsing."""
import json, re, os

def parse_llm_json_response(resp: str, default: dict = None) -> dict:
    """Strip markdown code fences and parse LLM JSON response."""
    try:
        cleaned = resp.strip()
        cleaned = re.sub(r"^```json\s*", "", cleaned)
        cleaned = re.sub(r"^```\s*", "", cleaned)
        cleaned = re.sub(r"```\s*$", "", cleaned)
        cleaned = cleaned.strip()
        return json.loads(cleaned)
    except json.JSONDecodeError:
        return default if default is not None else {}

def load_env():
    """Load .env file into environment variables."""
    env_file = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env")
    if os.path.exists(env_file):
        with open(env_file, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#') and '=' in line:
                    k, v = line.split('=', 1)
                    os.environ[k.strip()] = v.strip()