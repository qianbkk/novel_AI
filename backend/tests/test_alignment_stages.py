"""backend/tests/test_alignment_stages.py — Phase 6 跨端对齐验证

外部审计师在 commit 9821c2e 指出：原本「前后端对齐验证」只测了后端单侧。
这一组测试**真**做跨端对齐：

1. 静态对比：解析 frontend/src/pages/WorldBuild.tsx 内联的 FALLBACK_STAGES
   （TS 字符串用 Python regex），跟 backend/app/worldbuild/stages.py::STAGES
   比 key + label + 顺序。三项不一致就 fail。

2. 路由顺序断言：GET /worldbuild/stages 返的顺序应严格匹配 backend STAGES，
   且 labels 非空。这是审计师建议的顺序断言 + extra/backdoor 防护。

3. 双源 lockstep：FALLBACK_STAGES 一改 → 这条测试立刻挂。改动 FE 之后
   必须同步改 backend（或者反之），drift 被卡住。

为什么不直接复用 backend STAGES 喂给前端？
  - 离线首屏需要前端自给 fallback（前端不能 await 后端完成才渲染）
  - 但"首屏 fallback 必须跟 backend 真值一致"是 hard invariant——
    改了一端忘另一端 → 用户首屏跟 fetch resolve 后看到不同的 stage 列表
    → 进度条闪烁 / SSE 事件对不上 stage key
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

_BACKEND = Path(__file__).resolve().parent.parent
PROJECT_ROOT = _BACKEND.parent
FRONTEND_WORLD_BUILD = PROJECT_ROOT / "frontend" / "src" / "pages" / "WorldBuild.tsx"
BACKEND_STAGES = _BACKEND / "app" / "worldbuild" / "stages.py"


def _parse_frontend_fallback() -> list[tuple[str, str]]:
    """从 WorldBuild.tsx 里 regex 出 FALLBACK_STAGES 的 (key, label) 列表。

    容错：TS 格式会被解析；即使引号 / 缩进变化也工作（greedy 直到下一行）。
    """
    src = FRONTEND_WORLD_BUILD.read_text(encoding="utf-8")
    # 找 FALLBACK_STAGES 数组字面量
    m = re.search(
        r"FALLBACK_STAGES[^\[]*\[(.+?)\]",
        src, re.DOTALL,
    )
    if not m:
        raise RuntimeError(
            f"FALLBACK_STAGES 在 {FRONTEND_WORLD_BUILD} 找不到。TS 源格式可能变了。"
        )
    body = m.group(1)
    # 每行：{ key: "...", label: "..." }
    rows = re.findall(
        r'key:\s*"([^"]+)",\s*label:\s*"([^"]+)"',
        body,
    )
    return [(k, lbl) for k, lbl in rows]


def _parse_backend_stages() -> list[tuple[str, str]]:
    """从 backend/app/worldbuild/stages.py regex 出 STAGES 的 (key, label) 列表。

    跟前端 FALLBACK 不同，backend 的 STAGES 是 3-tuple (key, label, callable)，
    第三个元素是函数引用，我们只取前两个。
    """
    src = BACKEND_STAGES.read_text(encoding="utf-8")
    # 找 STAGES = [..] 块
    m = re.search(
        r"^STAGES:\s*list\[tuple\[str,\s*str,\s*callable\]\]\s*=\s*\[(.+?)\]",
        src, re.MULTILINE | re.DOTALL,
    )
    if not m:
        raise RuntimeError(
            f"backend STAGES 在 {BACKEND_STAGES} 找不到。Python 源格式可能变了。"
        )
    body = m.group(1)
    # 每行：("key", "label", callable_name)
    rows = re.findall(
        r'\(\s*"([^"]+)"\s*,\s*"([^"]+)"',
        body,
    )
    return [(k, lbl) for k, lbl in rows]


def test_fallback_keys_match_backend_stages():
    """FALLBACK_STAGES 的 keys 集合 == backend STAGES 的 keys 集合。

    防回归：drift in either direction → fail。
    """
    fe = _parse_frontend_fallback()
    be = _parse_backend_stages()

    fe_keys = {k for k, _ in fe}
    be_keys = {k for k, _ in be}

    extra_fe = fe_keys - be_keys
    extra_be = be_keys - fe_keys
    assert not extra_fe, f"FALLBACK_STAGES 多了 backend 没有的 key: {extra_fe}"
    assert not extra_be, (
        f"FALLBACK_STAGES 漏了 backend STAGES 的 key: {extra_be}。"
        f"（注意加了新 stage 必须同步两个源）"
    )


def test_fallback_order_matches_backend():
    """FALLBACK_STAGES 的顺序必须 == backend STAGES 的顺序（首屏渲染一致性）。"""
    fe = _parse_frontend_fallback()
    be = _parse_backend_stages()
    assert len(fe) == len(be), (
        f"长度不等：FE={len(fe)} / BE={len(be)}。"
        f"改 backend STAGES 忘改 FALLBACK_STAGES 会让 SSE stage_done 事件对不上"
    )
    for i, ((fe_k, _), (be_k, _)) in enumerate(zip(fe, be)):
        assert fe_k == be_k, (
            f"位置 {i}：FE key={fe_k!r}, BE key={be_k!r}。顺序漂移会让进度条顺序错位"
        )


def test_fallback_labels_match_backend():
    """FALLBACK_STAGES 的 labels 必须 == backend STAGES 的 labels（同源校验）。"""
    fe = _parse_frontend_fallback()
    be = _parse_backend_stages()
    fe_map = dict(fe)
    be_map = dict(be)
    # key 集合一致（上一条已断言过）
    for k in fe_map:
        assert fe_map[k] == be_map[k], (
            f"key={k!r} 的 label 漂移：FE={fe_map[k]!r} vs BE={be_map[k]!r}"
        )


def test_fallback_count_is_ten():
    """FALLBACK_STAGES 必须恰好 10 项 — 该常量一改，前端组件逻辑（5/10 显示等）就要同步审查。"""
    fe = _parse_frontend_fallback()
    assert len(fe) == 10, (
        f"FALLBACK_STAGES 应为 10 项，实际 {len(fe)}。"
        f"改这个数字前必须确认前端 UI 仍能处理（章节列表 / 进度条渲染逻辑）"
    )


def test_route_response_order_is_lockstep():
    """GET /worldbuild/stages 路由返回的顺序，必须严格等于 backend STAGES。

    顺序断言 + extra/backdoor 检查 — 直接覆盖审计师指出的 🟡-2/🟡-3：
    - 不仅验证 keys 集合
    - 还验证顺序
    - 还验证后端"未多出"任何字段
    """
    from fastapi.testclient import TestClient
    from app.main import app
    from app.worldbuild.stages import STAGES

    client = TestClient(app)
    try:
        r = client.get("/worldbuild/stages")
    finally:
        # 不让 side-effect 串扰到其他测试
        client.close()

    assert r.status_code == 200, r.text
    data = r.json()
    expected = [(s[0], s[1]) for s in STAGES]

    actual = [(s["key"], s["label"]) for s in data["stages"]]
    assert actual == expected, (
        f"后端响应顺序与 STAGES 不一致！\n"
        f"  expected={expected}\n"
        f"  actual  ={actual}"
    )

    # label 非空
    for s in data["stages"]:
        assert s.get("label"), f"stage {s.get('key')} label 为空"


def test_route_response_count_matches_stages_constant():
    """路由返 stages 数量 == backend STAGES 长度 — 防有人 STAGES 加项忘改路由。"""
    from fastapi.testclient import TestClient
    from app.main import app
    from app.worldbuild.stages import STAGES

    client = TestClient(app)
    try:
        r = client.get("/worldbuild/stages")
    finally:
        client.close()

    assert len(r.json()["stages"]) == len(STAGES), (
        f"路由返 {len(r.json()['stages'])} 项但 STAGES 有 {len(STAGES)} 项"
    )


def test_route_response_has_cache_control_header():
    """/worldbuild/stages 应带 Cache-Control 头 — STAGES 是不可变常量。

    auditor 🟢-4 建议：让浏览器/CDN 缓存 1 小时，减少每个组件挂载都重拉的
    浪费。STAGES 是发布期常量，max-age=3600 是合理折衷。
    """
    from fastapi.testclient import TestClient
    from app.main import app

    client = TestClient(app)
    try:
        r = client.get("/worldbuild/stages")
    finally:
        client.close()

    assert r.status_code == 200
    cache_control = r.headers.get("Cache-Control", "")
    assert "max-age=" in cache_control, (
        f"应带 max-age=... 缓存头，实际 Cache-Control={cache_control!r}"
    )
    # 解析最大 max-age（可能有多个）
    import re
    max_ages = re.findall(r"max-age=(\d+)", cache_control)
    assert max_ages, f"未找到 max-age=... 值：{cache_control!r}"
    assert int(max_ages[0]) >= 3600, (
        f"max-age 应 ≥ 3600（1 小时），实际={max_ages[0]}"
    )
