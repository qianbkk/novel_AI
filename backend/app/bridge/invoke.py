import asyncio
import json
import sys
import tempfile
from pathlib import Path
from typing import Awaitable, Callable

LineCallback = Callable[[str], Awaitable[None]]


async def invoke_novel_ai(
    novel_ai_dir: str,
    command: str,
    args: list[str] | None = None,
    role_overrides: dict[str, tuple[str, str]] | None = None,
    on_line: LineCallback | None = None,
) -> tuple[int, str]:
    args = args or []
    role_overrides = role_overrides or {}
    base_dir = Path(novel_ai_dir).resolve()
    run_py = base_dir / "run.py"
    if not run_py.exists():
        raise FileNotFoundError(f"novel_AI run.py not found: {run_py}")

    bootstrap = _bootstrap_source(base_dir, command, args, role_overrides)
    with tempfile.NamedTemporaryFile("w", suffix="_novel_ai_bootstrap.py", encoding="utf-8", delete=False) as f:
        f.write(bootstrap)
        bootstrap_path = Path(f.name)

    lines: list[str] = []
    try:
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            str(bootstrap_path),
            cwd=str(base_dir),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        assert process.stdout is not None
        while True:
            raw = await process.stdout.readline()
            if not raw:
                break
            line = raw.decode("utf-8", errors="replace").rstrip()
            lines.append(line)
            if on_line:
                await on_line(line)
        exit_code = await process.wait()
        return exit_code, "\n".join(lines)
    finally:
        try:
            bootstrap_path.unlink(missing_ok=True)
        except OSError:
            pass


def _bootstrap_source(
    base_dir: Path,
    command: str,
    args: list[str],
    role_overrides: dict[str, tuple[str, str]],
) -> str:
    argv = [str(base_dir / "run.py"), command, *args]
    routes_json = json.dumps(role_overrides, ensure_ascii=True)
    argv_json = json.dumps(argv, ensure_ascii=True)
    base_json = json.dumps(str(base_dir), ensure_ascii=True)
    return f"""
import json
import os
import runpy
import subprocess
import sys

BASE_DIR = {base_json}
sys.path.insert(0, BASE_DIR)

env_file = os.path.join(BASE_DIR, ".env")
if os.path.exists(env_file):
    with open(env_file, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                os.environ[key.strip()] = value.strip()

import api_client

api_client.MODEL_ROUTES.update({routes_json})
sys.argv = {argv_json}

def cmd_test():
    subprocess.run([sys.executable, os.path.join(BASE_DIR, "tools", "system_test.py")])

def cmd_calibrate():
    from tools.calibrate_checker import run_calibration
    run_calibration()

def cmd_fingerprint(rest):
    from tools.fingerprint_checker import cmd_check, cmd_scan
    if rest:
        cmd_check(rest[0])
    else:
        cmd_scan()

def cmd_acceptance(rest):
    from tools.acceptance_tests import run_all
    run_all()

def cmd_memory():
    from memory.memory_manager import check_memory_health
    from orchestrator_state import load_state
    state_path = os.path.join(BASE_DIR, "output", "orchestrator_state.json")
    state = load_state(state_path) if os.path.exists(state_path) else {{}}
    health = check_memory_health(state.get("novel_id", "renqingzhai_v1"))
    print(json.dumps(health, ensure_ascii=False, indent=2))

runpy.run_path(
    os.path.join(BASE_DIR, "run.py"),
    run_name="__main__",
    init_globals={{
        "cmd_test": cmd_test,
        "cmd_calibrate": cmd_calibrate,
        "cmd_fingerprint": cmd_fingerprint,
        "cmd_acceptance": cmd_acceptance,
        "cmd_memory": cmd_memory,
    }},
)
""".lstrip()
