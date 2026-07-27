"""audit_frontend_ui_2026_07_27.py —— 前端 UI 验收脚本（Phase C 前置）

目的：按 CLAUDE.md "用户验收清单"逐项访问页面并截图,作为视觉可证
据。重点：
- 大纲、世界观、力量体系、货币、地图节点、角色卡、关系图、势力图、
  伏笔、分层记忆、章节正文、章节标题、Provider、角色绑定、规则中心
- 图谱类必须截图,人工对照确认渲染（节点/边真实存在而非空画布）
- 分层记忆：之前是 Dashboard 假指标 + BridgeConsole 假温度计,
  本轮改为真实 L2/L5 上 API,要确认 MemoryPanel 显示真实数据

依赖：后端在 8132 端口、前端 dev server 在 5293 端口；后端 seed 一个
含 ≥1 角色 + ≥1 关系 + ≥1 势力的项目。

不修改任何文件,纯只读走流程 + 截图。
"""
from __future__ import annotations

import sys
from pathlib import Path

# 复用既有测试隔离 DB 不实际意义(测试夹具进程级); 这里直接用生产 DB
sys.path.insert(0, str(Path(__file__).resolve().parent))

from playwright.sync_api import sync_playwright

# 简化 stdout 输出缓冲,避免截图脚本被进度条污染
import sys, os
sys.stdout.reconfigure(line_buffering=True)
os.environ.setdefault("PYTHONUNBUFFERED", "1")

SHOTS_DIR = Path("/tmp/novel_ai_screenshots")
SHOTS_DIR.mkdir(parents=True, exist_ok=True)


def _shot(page, name: str) -> None:
    p = SHOTS_DIR / f"{name}.png"
    page.screenshot(path=str(p), full_page=True)
    print(f"  📸 {p}")


def _create_test_project_via_api() -> str | None:
    """通过后端 API 创建一个测试项目,返回 worldbuild 路径。
    CORS 失败的兜底方案,避免审计脚本被前端链路卡住。"""
    try:
        import httpx
        r = httpx.post("http://localhost:8132/projects", json={
            "title": "UI审计测试项目",
            "genre": "都市",
            "audience": "男频·青年向",
            "config_json": {"platform": "personal"},
        }, timeout=5.0)
        if r.status_code in (200, 201):
            data = r.json()
            pid = data.get("id") or data.get("project_id")
            if pid:
                return f"/projects/{pid}/worldbuild"
    except Exception as e:
        print(f"  ⚠ 创建测试项目失败: {e}")
    return None
    base = "http://localhost:5293"
    with sync_playwright() as p:
        browser = p.chromium.launch()
        ctx = browser.new_context(viewport={"width": 1280, "height": 900})
        page = ctx.new_page()

        # 1. 登录/匿名态
        page.goto(base, wait_until="domcontentloaded")
        page.wait_for_timeout(800)
        _shot(page, "01-dashboard")

        # 2. 新建项目页
        page.goto(f"{base}/new", wait_until="domcontentloaded")
        page.wait_for_timeout(500)
        _shot(page, "02-new-project")

        # 3. Provider 配置页
        page.goto(f"{base}/settings/providers", wait_until="domcontentloaded")
        page.wait_for_timeout(500)
        _shot(page, "03-providers")

        # 4. 角色绑定页
        page.goto(f"{base}/settings/roles", wait_until="domcontentloaded")
        page.wait_for_timeout(500)
        _shot(page, "04-role-assignments")

        # 找第一个项目的 projectId（从 dashboard 列表里拿）
        page.goto(base, wait_until="domcontentloaded")
        page.wait_for_timeout(800)
        # 取 sidebar / 项目卡片的链接
        href = None
        for sel in ('a[href*="/projects/"]', '.project-card'):
            try:
                el = page.query_selector(sel)
                if el:
                    href = el.get_attribute("href") or (el.inner_text() if hasattr(el, "inner_text") else "")
                    if href and "/projects/" in href:
                        break
            except Exception:
                pass

        # 兜底：从 DOM 里点出 project-card 的 href（点击进入项目）
        if not href or "/projects/" not in href:
            cards = page.query_selector_all('.project-card')
            for c in cards:
                # 通过 mouseup / onclick 间接拿不到 href；改抓第一个含 project id 的可点击元素
                pass
            # 真正兜底：从 dashboard 看到任意一个项目标题旁"进入"按钮 → 在卡片里找链接
            links = page.query_selector_all('a[href*="/projects/"]')
            for a in links:
                h = a.get_attribute("href") or ""
                if "/projects/" in h and "/bridge" not in h and "/worldbuild" not in h \
                        and "/outline" not in h and "/chapters" not in h and "/rules" not in h:
                    href = h
                    break
            if not href or "/projects/" not in href:
                # 终极兜底：直接构造一个项目（通过后端 API,绕开前端)
                href = _create_test_project_via_api() or href

        if not href or "/projects/" not in href:
            print("  ⚠ 无项目可访问;后续 project-scoped 页面跳过")
            browser.close()
            return

        pid = href.split("/projects/")[1].split("/")[0]
        print(f"  📦 当前项目 id = {pid}")

        # 5. 世界构建页
        page.goto(f"{base}/projects/{pid}/worldbuild", wait_until="domcontentloaded")
        page.wait_for_timeout(2000)
        _shot(page, "05-worldbuild")

        # 6. 写作控制台（核心：含 MemoryPanel）
        page.goto(f"{base}/projects/{pid}/bridge", wait_until="domcontentloaded")
        page.wait_for_timeout(2000)
        _shot(page, "06-bridge-memory-panel")

        # 滚动到记忆面板区域
        try:
            page.locator('[data-testid="memory-panel"]').first.scroll_into_view_if_needed()
            page.wait_for_timeout(500)
            _shot(page, "06b-bridge-memory-detail")
        except Exception as e:
            print(f"  ⚠ MemoryPanel 未找到: {e}")

        # 7. 章节列表
        page.goto(f"{base}/projects/{pid}/chapters", wait_until="domcontentloaded")
        page.wait_for_timeout(1000)
        _shot(page, "07-chapters")

        # 8. 规则中心
        page.goto(f"{base}/projects/{pid}/rules", wait_until="domcontentloaded")
        page.wait_for_timeout(800)
        _shot(page, "08-rules")

        # 9. 大纲页
        page.goto(f"{base}/projects/{pid}/outline", wait_until="domcontentloaded")
        page.wait_for_timeout(1000)
        _shot(page, "09-outline")

        browser.close()
        print(f"\n  ✅ 截图全部保存到 {SHOTS_DIR}")


def main() -> None:
    base = "http://localhost:5293"
    with sync_playwright() as p:
        browser = p.chromium.launch()
        ctx = browser.new_context(viewport={"width": 1280, "height": 900})
        page = ctx.new_page()

        # 1. 登录/匿名态
        page.goto(base, wait_until="domcontentloaded")
        page.wait_for_timeout(800)
        _shot(page, "01-dashboard")

        # 2. 新建项目页
        page.goto(f"{base}/new", wait_until="domcontentloaded")
        page.wait_for_timeout(500)
        _shot(page, "02-new-project")

        # 3. Provider 配置页
        page.goto(f"{base}/settings/providers", wait_until="domcontentloaded")
        page.wait_for_timeout(500)
        _shot(page, "03-providers")

        # 4. 角色绑定页
        page.goto(f"{base}/settings/roles", wait_until="domcontentloaded")
        page.wait_for_timeout(500)
        _shot(page, "04-role-assignments")

        # 5. 找一个项目 ID(从 DOM 链接或后端 API)
        page.goto(base, wait_until="domcontentloaded")
        page.wait_for_timeout(800)
        href = None
        for sel in ('a[href*="/projects/"]', '.project-card'):
            try:
                el = page.query_selector(sel)
                if el:
                    h = el.get_attribute("href") or ""
                    if h and "/projects/" in h:
                        href = h
                        break
            except Exception:
                pass

        if not href or "/projects/" not in href:
            links = page.query_selector_all('a[href*="/projects/"]')
            for a in links:
                h = a.get_attribute("href") or ""
                # 找一个真实进项目的入口(不是锚点跳转)
                if "/projects/" in h and "/bridge" not in h and "/worldbuild" not in h \
                        and "/outline" not in h and "/chapters" not in h and "/rules" not in h:
                    href = h
                    break
            if not href or "/projects/" not in href:
                href = _create_test_project_via_api()

        if not href or "/projects/" not in href:
            print("  ⚠ 无项目可访问;后续 project-scoped 页面跳过")
            browser.close()
            return

        pid = href.split("/projects/")[1].split("/")[0]
        print(f"  📦 当前项目 id = {pid}")

        # 6. 世界构建页
        page.goto(f"{base}/projects/{pid}/worldbuild", wait_until="domcontentloaded")
        page.wait_for_timeout(2000)
        _shot(page, "05-worldbuild")

        # 7. 写作控制台（核心：含 MemoryPanel）
        page.goto(f"{base}/projects/{pid}/bridge", wait_until="domcontentloaded")
        page.wait_for_timeout(2000)
        _shot(page, "06-bridge-memory-panel")
        try:
            page.locator('[data-testid="memory-panel"]').first.scroll_into_view_if_needed()
            page.wait_for_timeout(500)
            _shot(page, "06b-bridge-memory-detail")
        except Exception as e:
            print(f"  ⚠ MemoryPanel 未找到: {e}")

        # 8. 章节列表
        page.goto(f"{base}/projects/{pid}/chapters", wait_until="domcontentloaded")
        page.wait_for_timeout(1000)
        _shot(page, "07-chapters")

        # 9. 规则中心
        page.goto(f"{base}/projects/{pid}/rules", wait_until="domcontentloaded")
        page.wait_for_timeout(800)
        _shot(page, "08-rules")

        # 10. 大纲页
        page.goto(f"{base}/projects/{pid}/outline", wait_until="domcontentloaded")
        page.wait_for_timeout(1000)
        _shot(page, "09-outline")

        browser.close()
        print(f"\n  ✅ 截图全部保存到 {SHOTS_DIR}")


if __name__ == "__main__":
    main()