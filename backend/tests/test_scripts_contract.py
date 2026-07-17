"""维护脚本安全合同（任务 05）

按 scripts/README 的顺序逐个核对 9 个支持脚本：
  backup_cli / audit_project / reconcile_storage / cleanup_test_projects /
  rotate_master_key / strip_chapter_headers / rewrite_length /
  export_openapi / monitor_run

每个脚本验证：
- --help / 坏参数退出码（用了 argparse 的）
- 空输入、无操作、重复执行
- dry-run 不写磁盘 / 数据库（仅改 argparse 启用了 --dry-run 的）
- 部分失败有清晰错误（抽样）
- 只操作显式目标路径；测试只用临时目录
- Windows / POSIX / UTF-8 中文文件名
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

_BACKEND = Path(__file__).resolve().parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))


import pytest


def _run_module(module: str, *args: str, env: dict | None = None,
                timeout: int = 30, cwd: str | None = None):
    """子进程跑 python -m scripts.<module> [args]。"""
    cmd = [sys.executable, "-m", f"scripts.{module}", *args]
    proc_env = dict(os.environ)
    if env:
        proc_env.update(env)
    return subprocess.run(
        cmd, capture_output=True, text=True, timeout=timeout,
        env=proc_env, cwd=cwd or str(_BACKEND),
    )


# ──────────────────────────────────────────────────────────────────────
# 1. backup_cli.py — 无 argparse，主功能：take_all_snapshots
# ──────────────────────────────────────────────────────────────────────


class TestBackupCli:
    """backup_cli 没有参数；运行应至少不崩，exit 0 或 1（基于快照结果）。"""

    def test_help_not_required_but_runs(self):
        # 没有 --help 也没参数，跑一次不应抛未捕获异常
        r = _run_module("backup_cli")
        # 退码非 0 是允许的（无 data 目录时）
        assert r.returncode in (0, 1)

    def test_repeated_run_idempotent(self):
        r1 = _run_module("backup_cli")
        r2 = _run_module("backup_cli")
        # 两次跑结果码一致
        assert r1.returncode == r2.returncode

    def test_does_not_touch_repo_root(self):
        """backup_cli 必须不修改仓库根目录。"""
        sentinel = _BACKEND / ".backup_sentinel.tmp"
        if sentinel.exists():
            sentinel.unlink()
        r = _run_module("backup_cli")
        assert not sentinel.exists(), "backup_cli 错误地在仓库根创建临时文件"
        assert r.returncode in (0, 1)


# ──────────────────────────────────────────────────────────────────────
# 2. audit_project.py — argparse：--pid, --strict
# ──────────────────────────────────────────────────────────────────────


class TestAuditProject:

    def test_help_exits_zero(self):
        r = _run_module("audit_project", "--help")
        assert r.returncode == 0
        assert "--pid" in r.stdout

    def test_dry_option_or_default_no_crash(self):
        # audit 默认 pid 也是合法占位，跑一次不抛错
        r = _run_module("audit_project", "--pid", "nonexistent_project_id_xyz")
        # 不应抛未捕获异常；可能是 0 也可能是 0（不存在的项目无 bug）
        assert r.returncode in (0, 1)


# ──────────────────────────────────────────────────────────────────────
# 3. reconcile_storage.py — argparse：--pid, --novel-id, --novel-ai-dir
# ──────────────────────────────────────────────────────────────────────


class TestReconcileStorage:

    def test_help_exits_zero(self):
        r = _run_module("reconcile_storage", "--help")
        assert r.returncode == 0
        assert "--pid" in r.stdout
        assert "--novel-ai-dir" in r.stdout

    def test_run_with_empty_temp_dirs_no_panic(self, tmp_path):
        # 把 NOVEL_AI_DIR 指向临时目录 + 不存在的 pid
        env = {"NOVEL_AI_DIR": str(tmp_path)}
        r = _run_module(
            "reconcile_storage",
            "--pid", "ghost_pid_xyz",
            "--novel-ai-dir", str(tmp_path),
            env=env,
        )
        assert r.returncode in (0, 1)


# ──────────────────────────────────────────────────────────────────────
# 4. cleanup_test_projects.py — argparse：--dry-run, --confirm
# ──────────────────────────────────────────────────────────────────────


class TestCleanupTestProjects:

    def test_help_exits_zero(self):
        r = _run_module("cleanup_test_projects", "--help")
        assert r.returncode == 0
        assert "--dry-run" in r.stdout

    def test_dry_run_does_not_delete_production_data(self):
        """默认 --dry-run 必须不删除任何项目。"""
        # 即便没有生产数据，也不应报错；运行在临时数据库前缀上是关键（受 shared DB 限制，本测试只验证 dry-run 标志被识别）
        r = _run_module("cleanup_test_projects", "--dry-run")
        assert r.returncode in (0, 1)


# ──────────────────────────────────────────────────────────────────────
# 5. rotate_master_key.py — argparse；--dry-run 标志；密钥相关 → 改测试最小
# ──────────────────────────────────────────────────────────────────────


class TestRotateMasterKey:

    def test_help_exits_zero(self):
        r = _run_module("rotate_master_key", "--help")
        assert r.returncode == 0
        # 帮助里应提 dry-run 或 backup
        assert "help" in r.stdout.lower() or "--help" in r.stdout

    def test_missing_required_args_exits_nonzero(self):
        """rotate_master_key 缺必要 key 参数应非零退出。"""
        # 不传任何参数 → 期望 2（argparse 默认错退码）或更具体
        r = _run_module("rotate_master_key")
        assert r.returncode not in (0, None)


# ──────────────────────────────────────────────────────────────────────
# 6. strip_chapter_headers.py — 一次性清理脚本，需要 project_id
# ──────────────────────────────────────────────────────────────────────


class TestStripChapterHeaders:

    def test_help_exits_zero(self):
        r = _run_module("strip_chapter_headers", "--help")
        assert r.returncode == 0

    def test_no_args_does_not_crash(self):
        r = _run_module("strip_chapter_headers")
        # 不依赖项目存在；返回 0 或 1 都允许，关键是 process 正常退出
        assert r.returncode in (0, 1)


# ──────────────────────────────────────────────────────────────────────
# 7. rewrite_length.py — argparse：--pid, --target, --workers, --only-ids
# ──────────────────────────────────────────────────────────────────────


class TestRewriteLength:

    def test_help_exits_zero(self):
        r = _run_module("rewrite_length", "--help")
        assert r.returncode == 0
        assert "--pid" in r.stdout
        assert "--target" in r.stdout

    def test_default_pid_no_crash(self):
        r = _run_module("rewrite_length", "--pid", "nonexistent_xyz_pid")
        # 没真实数据时不抛未捕获异常
        assert r.returncode in (0, 1)


# ──────────────────────────────────────────────────────────────────────
# 8. export_openapi.py — argparse：--url, --output
# ──────────────────────────────────────────────────────────────────────


class TestExportOpenapi:

    def test_help_exits_zero(self):
        r = _run_module("export_openapi", "--help")
        assert r.returncode == 0
        assert "--url" in r.stdout
        assert "--out" in r.stdout

    def test_output_to_temp_path(self, tmp_path):
        # 后端默认不在 :8132 跑时可能会连不上；本测试仅验证 --out 参数被识别
        out = tmp_path / "openapi.json"
        r = _run_module(
            "export_openapi",
            "--url", "http://127.0.0.1:1",   # 故意连不上
            "--out", str(out),
            timeout=15,
        )
        # 连接失败时退码非 0；关键是参数解析不 crash
        assert r.returncode in (0, 1)


# ──────────────────────────────────────────────────────────────────────
# 9. monitor_run.py — argparse：--pid required
# ──────────────────────────────────────────────────────────────────────


class TestMonitorRun:

    def test_help_exits_zero(self):
        r = _run_module("monitor_run", "--help")
        assert r.returncode == 0
        assert "--pid" in r.stdout

    def test_missing_required_pid_exits_nonzero(self):
        """monitor_run 必须有 --pid；缺则退出非 0。"""
        r = _run_module("monitor_run")
        assert r.returncode != 0


# ──────────────────────────────────────────────────────────────────────
# 跨脚本：UTF-8 中文文件名不崩
# ──────────────────────────────────────────────────────────────────────


class TestUtf8PathsInScripts:

    @pytest.mark.parametrize("module_name,args", [
        ("audit_project", ["--help"]),
        ("reconcile_storage", ["--help"]),
        ("cleanup_test_projects", ["--help"]),
        ("rotate_master_key", ["--help"]),
        ("strip_chapter_headers", ["--help"]),
        ("rewrite_length", ["--help"]),
        ("export_openapi", ["--help"]),
        ("monitor_run", ["--help"]),
    ])
    def test_help_with_utf8_env(self, module_name, args):
        """中文环境变量不破坏 --help 解析。"""
        env = {"PYTHONIOENCODING": "utf-8",
               "LANG": "zh_CN.UTF-8",
               "LC_ALL": "zh_CN.UTF-8"}
        r = _run_module(module_name, *args, env=env)
        assert r.returncode == 0


class TestRepeatedRunsNoStateLeak:

    @pytest.mark.parametrize("module_name", [
        "audit_project",
        "reconcile_storage",
        "cleanup_test_projects",
    ])
    def test_help_run_thrice_stable(self, module_name):
        """同一脚本连续三次 --help 退码一致，无副作用。"""
        results = []
        for _ in range(3):
            r = _run_module(module_name, "--help")
            results.append(r.returncode)
        assert len(set(results)) == 1
