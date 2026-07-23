"""drive_30ch_bridge.py — 按前端按钮一致流程驱动 bridge 跑 30 章。

用法：
    cd backend
    python -m scripts.drive_30ch_bridge <project_id> [--api http://127.0.0.1:8132]
                                          [--chapters 30] [--outline-mode batch]

完整流水线（与前端 BridgeConsole 按钮一致）：
    1. PUT /bridge/binding       — 绑定项目专属 engine 数据目录
    2. POST /bridge/push-concept — 推送设定包
    3. POST /bridge/run planner  — 生成设定包（planner agent）
    4. POST /bridge/pull-setting — 回灌 setting_package 到 DB
    5. POST /bridge/run bootstrap — 黄金三章（自动选最优版本）
    6. POST /bridge/run init_arc  — 初始化 arc_plans
    7. POST /bridge/run run N    — 跑 N 章
    8. POST /bridge/import-chapters — 章节入库

约束遵守：
    - 不用 engine.tools.select_version / engine.tools.init_arc 直接调用
      （前者由 bootstrap 自动选最优版代替；后者用 bridge.run init_arc 命令）
    - 全程走 HTTP API + SSE 流，与前端操作一致
    - 不修改 DB Schema、不写 phase/iteration 报告

设计：复用 run_mvp.py 的 SSE 流式解析 + 改造为分步驱动。
"""
import argparse
import json
import sys
import time
from pathlib import Path
from urllib.parse import urlencode

import httpx

BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))


def stream_sse(client: httpx.Client, url: str, label: str) -> dict:
    """拉 SSE 流直到 done 事件，返回 final payload。"""
    print(f"\n{'=' * 60}\n[{label}] start\n{'=' * 60}")
    final = {}
    with client.stream("GET", url) as resp:
        for line in resp.iter_lines():
            if not line.startswith("data: "):
                continue
            try:
                ev = json.loads(line[6:])
            except json.JSONDecodeError:
                continue
            et = ev.get("event", "?")
            if et == "log":
                print(f"  {ev.get('line', '').rstrip()}")
            elif et == "node_start":
                print(f"  → {ev.get('node', '?')}")
            elif et == "node_end":
                print(f"  ← {ev.get('node', '?')}")
            elif et == "auto_pull_setting_done":
                print(f"  ✓ auto pull-setting done")
            elif et == "auto_import_chapters_done":
                n = len(ev.get("imported", []))
                print(f"  ✓ auto import-chapters done ({n} chapters)")
            elif et == "error":
                print(f"  ✗ ERROR: {ev.get('message', '')[:200]}")
            elif et == "done":
                final = ev
            elif et == "complete":
                print(f"  ✓ complete (status={ev.get('status', '?')}, exit_code={ev.get('exit_code', '?')})")
    return final


def bridge_run(client: httpx.Client, project_id: str, command: str,
               args: list[str], outline_mode: str = "batch") -> dict:
    """POST /bridge/run + 流式拉 SSE。"""
    r = client.post(
        f"/projects/{project_id}/bridge/run",
        json={"command": command, "args": args, "outline_mode": outline_mode},
    )
    r.raise_for_status()
    run = r.json()
    run_id = run["id"]
    label = f"{command} {' '.join(args)}".strip()
    final = stream_sse(
        client,
        f"/projects/{project_id}/bridge/stream?{urlencode({'run_id': run_id})}",
        label,
    )
    return final


def setup_binding(client: httpx.Client, project_id: str, novel_ai_dir: str) -> None:
    """PUT /bridge/binding — 为该项目创建专属 engine 数据目录。"""
    r = client.put(
        f"/projects/{project_id}/bridge/binding",
        json={"novel_ai_dir": novel_ai_dir, "novel_id": project_id},
    )
    r.raise_for_status()
    print(f"\n[binding] project={project_id[:8]} novel_ai_dir={novel_ai_dir}")


def main() -> int:
    p = argparse.ArgumentParser(description="驱动 bridge 跑 N 章")
    p.add_argument("project_id", help="目标 project_id")
    p.add_argument("--api", default="http://127.0.0.1:8132")
    p.add_argument("--chapters", type=int, default=30)
    p.add_argument("--outline-mode", default="batch",
                   help="batch (默认) | card | talk")
    p.add_argument("--skip-bootstrap", action="store_true",
                   help="跳过 bootstrap + init_arc（仅在已跑过的情况下用）")
    args = p.parse_args()

    started = time.time()
    summary = {"steps": []}

    # 项目专属 engine 数据目录：每个项目一个子目录，状态 / 章节互不串味
    backend_root = Path(__file__).resolve().parent.parent
    novel_ai_dir = str(backend_root / "data" / "engine" / "project" / args.project_id)
    Path(novel_ai_dir, "output", "chapters").mkdir(parents=True, exist_ok=True)
    Path(novel_ai_dir, "config").mkdir(parents=True, exist_ok=True)
    Path(novel_ai_dir, "memory", "l2").mkdir(parents=True, exist_ok=True)
    Path(novel_ai_dir, "logs").mkdir(parents=True, exist_ok=True)

    with httpx.Client(base_url=args.api, timeout=600.0) as client:
        # 0. health check
        try:
            client.get("/health").raise_for_status()
        except Exception as e:
            print(f"✗ 后端 {args.api} 不可达：{e}")
            return 1

        # 1. binding
        try:
            setup_binding(client, args.project_id, novel_ai_dir)
            summary["steps"].append({"step": "binding", "ok": True})
        except httpx.HTTPStatusError as e:
            print(f"✗ binding 失败: {e.response.status_code} {e.response.text[:200]}")
            return 1

        # 2. push-concept
        try:
            r = client.post(f"/projects/{args.project_id}/bridge/push-concept")
            r.raise_for_status()
            print(f"\n[1/7] push-concept OK")
            summary["steps"].append({"step": "push-concept", "ok": True})
        except httpx.HTTPStatusError as e:
            print(f"✗ push-concept 失败: {e.response.status_code} {e.response.text[:200]}")
            if "worldbuild" in e.response.text:
                print("  提示: project.status != ready；先跑 continue_worldbuild")
            return 1

        # 3. planner
        final = bridge_run(client, args.project_id, "planner", [], args.outline_mode)
        if final.get("exit_code") != 0:
            print(f"✗ planner 失败 (exit_code={final.get('exit_code')})")
            return 1
        summary["steps"].append({"step": "planner", "exit_code": 0})

        # 4. pull-setting（planner 完成后通常自动回灌；显式再调一次兜底）
        time.sleep(2)
        try:
            r = client.post(f"/projects/{args.project_id}/bridge/pull-setting")
            r.raise_for_status()
            print(f"\n[3/7] pull-setting OK")
            summary["steps"].append({"step": "pull-setting", "ok": True})
        except Exception as e:
            print(f"⚠ pull-setting 失败: {e}")

        # 5. bootstrap + init_arc
        if not args.skip_bootstrap:
            final = bridge_run(client, args.project_id, "bootstrap", [], args.outline_mode)
            if final.get("exit_code") != 0:
                print(f"✗ bootstrap 失败 (exit_code={final.get('exit_code')})")
                return 1
            summary["steps"].append({"step": "bootstrap", "exit_code": 0})

            final = bridge_run(client, args.project_id, "init_arc", [], args.outline_mode)
            if final.get("exit_code") != 0:
                print(f"✗ init_arc 失败 (exit_code={final.get('exit_code')})")
                return 1
            summary["steps"].append({"step": "init_arc", "exit_code": 0})
        else:
            print(f"\n[4-5/7] bootstrap + init_arc (skipped)")

        # 6. run N
        final = bridge_run(
            client, args.project_id, "run",
            [str(args.chapters)], args.outline_mode,
        )
        if final.get("exit_code") != 0:
            print(f"✗ run {args.chapters} 失败 (exit_code={final.get('exit_code')})")
            return 1
        summary["steps"].append({"step": f"run {args.chapters}", "exit_code": 0})

        # 7. import-chapters
        try:
            r = client.post(f"/projects/{args.project_id}/bridge/import-chapters")
            r.raise_for_status()
            imported = r.json()
            print(f"\n[7/7] import-chapters OK (导入 {len(imported)} 章)")
            summary["steps"].append({"step": "import-chapters", "imported": len(imported)})
        except Exception as e:
            print(f"⚠ import-chapters 失败: {e}")

    elapsed = time.time() - started
    print(f"\n{'=' * 60}\n跑完！总耗时 {elapsed:.1f}s\n{'=' * 60}")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())