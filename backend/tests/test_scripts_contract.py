"""Side-effect-free contracts for supported maintenance CLIs."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest


BACKEND_ROOT = Path(__file__).resolve().parent.parent


def _run_module(module: str, *args: str, env: dict[str, str] | None = None):
    process_env = dict(os.environ)
    process_env.update(env or {})
    return subprocess.run(
        [sys.executable, "-m", f"scripts.{module}", *args],
        cwd=BACKEND_ROOT,
        env=process_env,
        capture_output=True,
        text=True,
        timeout=15,
    )


@pytest.mark.parametrize(
    "module",
    [
        "audit_project",
        "reconcile_storage",
        "cleanup_test_projects",
        "rotate_master_key",
        "strip_chapter_headers",
        "rewrite_length",
        "export_openapi",
        "monitor_run",
    ],
)
def test_argparse_scripts_expose_help(module):
    result = _run_module(module, "--help", env={"PYTHONIOENCODING": "utf-8"})
    assert result.returncode == 0, result.stderr
    assert "usage:" in result.stdout.lower()


def test_strip_headers_requires_explicit_directory():
    result = _run_module("strip_chapter_headers")
    assert result.returncode == 2
    assert "--chapters-dir" in result.stderr


def test_strip_headers_dry_run_and_apply_are_explicit(tmp_path):
    from scripts.strip_chapter_headers import clean_file

    chapter = tmp_path / "ch_0001.txt"
    original = "第1章 重复标题\n真正正文\n"
    chapter.write_text(original, encoding="utf-8")

    count, removed = clean_file(chapter)
    assert (count, removed) == (1, ["第1章 重复标题"])
    assert chapter.read_text(encoding="utf-8") == original

    count, _ = clean_file(chapter, apply=True)
    assert count == 1
    assert chapter.read_text(encoding="utf-8") == "真正正文\n"


@pytest.mark.parametrize("module", ["rotate_master_key", "monitor_run"])
def test_required_arguments_fail_without_touching_data(module):
    result = _run_module(module)
    assert result.returncode == 2
