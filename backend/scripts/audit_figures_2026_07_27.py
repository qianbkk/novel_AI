"""audit_figures_2026_07_27.py —— 真实图谱渲染审计

找一个已 ready 的项目(已有的),进入 WorldBuild 验证:
- 关系图(RelationGraph) SVG 节点真实渲染
- 势力图(FactionGraph) 接真实边(不再 i%5 合成)、faction=0 不再画孤立圆点
- 记忆面板在 BridgeConsole 真实数据下显示

Phase C 用户验收要求"图谱类必须截图识图"。
"""
from __future__ import annotations
import sys, os
from pathlib import Path

sys.stdout.reconfigure(line_buffering=True)
os.environ.setdefault("PYTHONUNBUFFERED", "1")

from playwright.sync_api import sync_playwright

SHOTS = Path("/tmp/novel_ai_screenshots")
SHOTS.mkdir(parents=True, exist_ok=True)


def main() -> None:
    base = "http://localhost:5293"
    with sync_playwright() as p:
        browser = p.chromium.launch()
        ctx = browser.new_context(viewport={"width": 1400, "height": 1100})
        page = ctx.new_page()

        # 从 API 找一个 status=ready 的项目
        page.goto(base, wait_until="domcontentloaded")
        page.wait_for_timeout(800)

        ready_pid = page.evaluate("""async () => {
            try {
                const r = await fetch('http://localhost:8132/projects');
                if (!r.ok) return null;
                const list = await r.json();
                const ready = list.find(x => x.status === 'ready');
                return ready ? ready.id : null;
            } catch(e) { return null; }
        }""")
        if not ready_pid:
            # fallback: 取第一个
            any_pid = page.evaluate("""async () => {
                try {
                    const r = await fetch('http://localhost:8132/projects');
                    if (!r.ok) return null;
                    const list = await r.json();
                    return list.length ? list[0].id : null;
                } catch(e) { return null; }
            }""")
            ready_pid = any_pid
            print(f"  ⚠ 没找到 ready 项目;用第一个: {ready_pid}")
        else:
            print(f"  📦 找到 ready 项目: {ready_pid}")

        if not ready_pid:
            print("  ❌ 无项目")
            browser.close()
            return

        # 1. WorldBuild —— 关键:验证图谱 + 大数据
        page.goto(f"{base}/projects/{ready_pid}/worldbuild", wait_until="domcontentloaded")
        page.wait_for_timeout(2500)
        page.screenshot(path=str(SHOTS / "F1-worldbuild-full.png"), full_page=True)

        # 切到"阵营/势力"tab
        for tab_text in ("阵营", "势力", "人物阵营", "factions"):
            try:
                page.get_by_role("button", name=tab_text, exact=False).first.click()
                page.wait_for_timeout(800)
                break
            except Exception:
                pass
        page.screenshot(path=str(SHOTS / "F2-worldbuild-factions.png"), full_page=True)

        # 2. 关系图 —— 单独一个组件,在某页面里
        # 先看看 WorldBuild 有没有"人物关系"tab
        for tab_text in ("人物关系", "关系", "relations", "character"):
            try:
                page.get_by_role("button", name=tab_text, exact=False).first.click()
                page.wait_for_timeout(800)
                break
            except Exception:
                pass
        page.screenshot(path=str(SHOTS / "F3-worldbuild-relations.png"), full_page=True)

        # 3. 章节管理
        page.goto(f"{base}/projects/{ready_pid}/chapters", wait_until="domcontentloaded")
        page.wait_for_timeout(1500)
        page.screenshot(path=str(SHOTS / "F4-chapters.png"), full_page=True)

        # 4. 大纲
        page.goto(f"{base}/projects/{ready_pid}/outline", wait_until="domcontentloaded")
        page.wait_for_timeout(1500)
        page.screenshot(path=str(SHOTS / "F5-outline.png"), full_page=True)

        # 5. Bridge Console —— 真实数据下的 MemoryPanel
        page.goto(f"{base}/projects/{ready_pid}/bridge", wait_until="domcontentloaded")
        page.wait_for_timeout(2500)
        page.screenshot(path=str(SHOTS / "F6-bridge-top.png"), full_page=False)
        # 展开"实时记忆层"details
        try:
            page.locator("summary").filter(has_text="实时记忆层").first.click()
            page.wait_for_timeout(800)
        except Exception:
            pass
        page.screenshot(path=str(SHOTS / "F6b-bridge-memory-opened.png"), full_page=True)

        browser.close()
        print(f"\n  ✅ 全部保存到 {SHOTS}")


if __name__ == "__main__":
    main()