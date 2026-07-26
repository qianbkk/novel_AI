"""test_paths_resolution_2026_07_26.py

架构审视 — tests/_paths.py 仓库根解析加固。

真实踩坑（2026-07-26）：
某次 e2e 跑出的相对路径 bug 在 `backend/` 下留了个游离的 `backend/data/` 目录。
于是 `backend/` 自己同时满足了 find_repo_root() 的两个 marker（有 .gitignore、
有 backend/ 子目录），REPO_ROOT 被静默解析成 `backend/` 而不是仓库根。

后果是 18 个结构不变量测试同时失败，报错却是
`FileNotFoundError: ...\\backend\\README.md` —— 看起来像业务代码回归，
实际是路径解析被一个空目录带偏，排查成本极高。

修法：仓库根判定加反向约束 —— 自身不能长得像 backend 根（含 app/ + engine/）。

覆盖：
- 正常仓库布局能定位到仓库根
- 出现游离 backend/backend/ 时仍能跳过、找到真正的仓库根（核心回归）
- REPO_ROOT 与 BACKEND_ROOT 在真实仓库里必须是不同的两个目录
- 找不到时 fail-loud 而不是返回错的路径
"""
from __future__ import annotations

from pathlib import Path

import pytest

from tests._paths import (
    BACKEND_ROOT,
    REPO_ROOT,
    _looks_like_backend_root,
    find_backend_root,
    find_repo_root,
)


def _make_repo(root: Path) -> Path:
    """搭一个最小的仓库布局：root/.gitignore + root/backend/{app,engine}。"""
    (root / ".gitignore").write_text("data/\n", encoding="utf-8")
    backend = root / "backend"
    (backend / "app").mkdir(parents=True)
    (backend / "engine").mkdir(parents=True)
    (backend / "tests").mkdir(parents=True)
    return backend


# ─── 1. 正常布局 ─────────────────────────

def test_finds_repo_root_in_clean_layout(tmp_path):
    backend = _make_repo(tmp_path)
    start = backend / "tests" / "_paths.py"
    assert find_repo_root(start) == tmp_path


def test_finds_backend_root_in_clean_layout(tmp_path):
    backend = _make_repo(tmp_path)
    start = backend / "tests" / "_paths.py"
    assert find_backend_root(start) == backend


# ─── 2. 游离 backend/backend/ 的核心回归 ─────────────────────────

def test_stray_nested_backend_does_not_hijack_repo_root(tmp_path):
    """backend/ 下出现 backend/ 子目录 + backend/.gitignore 时，
    仓库根必须仍然解析到真正的仓库根，而不是 backend/ 自己。"""
    backend = _make_repo(tmp_path)
    # 复现踩坑现场：backend 自己有 .gitignore，且多出一个游离的 backend/ 子目录
    (backend / ".gitignore").write_text("data/\n", encoding="utf-8")
    (backend / "backend" / "data").mkdir(parents=True)

    start = backend / "tests" / "_paths.py"
    assert find_repo_root(start) == tmp_path, "游离目录把仓库根带偏了"


def test_stray_nested_backend_keeps_backend_root_correct(tmp_path):
    """同一场景下 backend 根不受影响。"""
    backend = _make_repo(tmp_path)
    (backend / ".gitignore").write_text("data/\n", encoding="utf-8")
    (backend / "backend" / "data").mkdir(parents=True)

    start = backend / "tests" / "_paths.py"
    assert find_backend_root(start) == backend


def test_repo_root_and_backend_root_differ_in_stray_case(tmp_path):
    """踩坑时两者会重合 —— 这是最容易看漏的症状，单独锁一条。"""
    backend = _make_repo(tmp_path)
    (backend / ".gitignore").write_text("data/\n", encoding="utf-8")
    (backend / "backend" / "data").mkdir(parents=True)

    start = backend / "tests" / "_paths.py"
    assert find_repo_root(start) != find_backend_root(start)


# ─── 3. 真实仓库的当前状态 ─────────────────────────

def test_real_repo_root_is_parent_of_backend_root():
    """当前仓库里 REPO_ROOT 必须是 BACKEND_ROOT 的父目录。"""
    assert REPO_ROOT != BACKEND_ROOT
    assert Path(BACKEND_ROOT).parent == Path(REPO_ROOT)


def test_real_repo_root_has_expected_markers():
    """定位到的仓库根必须真的含 README.md 与 frontend/（结构断言依赖它们）。"""
    root = Path(REPO_ROOT)
    assert (root / "README.md").is_file()
    assert (root / "frontend").is_dir()
    assert (root / "backend").is_dir()


# ─── 4. helper 与 fail-loud ─────────────────────────

def test_looks_like_backend_root_positive(tmp_path):
    (tmp_path / "app").mkdir()
    (tmp_path / "engine").mkdir()
    assert _looks_like_backend_root(tmp_path) is True


@pytest.mark.parametrize("dirs", [(), ("app",), ("engine",)])
def test_looks_like_backend_root_negative(tmp_path, dirs):
    for d in dirs:
        (tmp_path / d).mkdir()
    assert _looks_like_backend_root(tmp_path) is False


def test_find_repo_root_raises_when_absent(tmp_path):
    """找不到 marker 时必须显式抛错，不能默默返回一个错的目录。"""
    lonely = tmp_path / "nowhere" / "deep"
    lonely.mkdir(parents=True)
    with pytest.raises(RuntimeError, match="无法定位仓库根"):
        find_repo_root(lonely / "x.py")


def test_find_backend_root_raises_when_absent(tmp_path):
    lonely = tmp_path / "nowhere" / "deep"
    lonely.mkdir(parents=True)
    with pytest.raises(RuntimeError, match="无法定位 backend 根"):
        find_backend_root(lonely / "x.py")
