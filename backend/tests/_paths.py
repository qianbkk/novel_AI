"""backend/tests/_paths.py — 可靠定位仓库根 / backend 根的 helper

历史背景（Phase D）：
  多个 test_invariants 用 Path(__file__).resolve().parents[N] 写死深度假设
  — 把原单文件不变量测试拆成 tests/invariants/test_X.py
  子包后，每个 test_X.py 实际在 backend/tests/invariants/，比原来
  backend/tests/ 多 1 层路径深度。所有 parents[N] 写死假设的测试都错位
  （parents[1] 期望 backend 实际是 backend/tests；parents[2] 期望 repo_root
  实际是 backend）。

  之前依赖调用时 cwd 是 backend/ 才能让部分测试凑巧通过 — 一旦从仓库根
  从仓库根运行 pytest 时，测试甚至无法收集
  （ModuleNotFoundError: 'tests.invariants'）。

修法：定义 find_repo_root() / find_backend_root() 通过向上走找 marker 文件
定位（不依赖文件深度，也不依赖 cwd）。

Marker 文件选择：
  - 仓库根：.gitignore（仓库根必有） + 目录 backend/
  - backend 根：含 app/ subdir + engine/ subdir
"""
from __future__ import annotations

from pathlib import Path

# 缓存：模块级首次解析后复用（test session 启动期稳定）
_REPO_ROOT: Path | None = None
_BACKEND_ROOT: Path | None = None


def find_repo_root(start: Path | None = None) -> Path:
    """从 start（或 __file__ 默认）向上走，找含 .gitignore 和 backend/ 的目录。

    仓库根的特征：自身含 .gitignore + 含 backend/ 子目录。
    为什么用 .gitignore 而不是 .git：.gitignore 是用户可见文件（无
    .git 时也常见 — 比如 svn 仓库或只是 zip 目录），稳定性更好。
    """
    global _REPO_ROOT
    if _REPO_ROOT is not None and start is None:
        return _REPO_ROOT
    cur = (start or Path(__file__).resolve()).parent
    while cur != cur.parent:
        # 典型 marker：仓库根有 .gitignore + backend/ 子目录
        if (cur / ".gitignore").exists() and (cur / "backend").is_dir():
            if start is None:
                _REPO_ROOT = cur
            return cur
        cur = cur.parent
    raise RuntimeError(
        f"无法定位仓库根（从 {start or Path(__file__)} 向上未找到 "
        f"含 .gitignore + backend/ 的目录）"
    )


def find_backend_root(start: Path | None = None) -> Path:
    """向上走找 backend/ 目录（其父目录是仓库根）。

    backend/ 的特征：自身含 app/ + engine/ 子目录。
    """
    global _BACKEND_ROOT
    if _BACKEND_ROOT is not None and start is None:
        return _BACKEND_ROOT
    cur = (start or Path(__file__).resolve()).parent
    while cur != cur.parent:
        if (cur / "app").is_dir() and (cur / "engine").is_dir():
            if start is None:
                _BACKEND_ROOT = cur
            return cur
        cur = cur.parent
    raise RuntimeError(
        f"无法定位 backend 根（从 {start or Path(__file__)} 向上未找到 "
        f"含 app/ + engine/ 的目录）"
    )


# 便捷：模块级常量（首次访问时解析）
REPO_ROOT = find_repo_root()
BACKEND_ROOT = find_backend_root()
