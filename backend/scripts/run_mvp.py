"""run_mvp.py — 一键跑完整 MVP 流水线

用法:
    cd backend
    python -m scripts.run_mvp <project_id> [--api http://localhost:8123] [--chapters 1] [--select A]

跑法:
    1. push-concept          推世界构建到 novel_AI/config/novel_config.json
    2. planner               生成设定包
    3. pull-setting          回灌到 DB
    4. bootstrap             黄金三章 A/B/C
    5. select 1 A            选第 1 章版本 A（可通过 --select 改）
    6. run N                 写 N 章（--chapters，默认 1）
    7. import-chapters       章节导入 DB

每步流式打印 SSE 日志，最后给摘要。
"""
import argparse
import json
import os
import sys
import time
from pathlib import Path
from urllib.parse import urlencode

import httpx

BACKEND_ROOT = Path(__file__).resolve().parent.parent
NOVEL_AI_DIR = BACKEND_ROOT.parent / "novel_AI"


def stream_sse(client: httpx.Client, url: str, run_id: str, label: str) -> dict:
    """拉 SSE 流直到 done 事件，返回 done 事件的完整 payload。"""
    print(f"\n{'='*60}\n[{label}] start\n{'='*60}")
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
                imported = ev.get("imported", [])
                print(f"  ✓ auto import-chapters done ({len(imported)} chapters)")
            elif et == "error":
                print(f"  ✗ ERROR: {ev.get('message', '')}")
            elif et == "done":
                final = ev
            elif et == "complete":
                print(f"  ✓ complete (status={ev.get('status', '?')}, exit_code={ev.get('exit_code', '?')})")
    print(f"[{label}] done: exit_code={final.get('exit_code', '?')}")
    return final


def call_bridge_run(client: httpx.Client, project_id: str, command: str, args: list[str]) -> dict:
    """POST /bridge/run + 流式拉 SSE。"""
    r = client.post(
        f"/projects/{project_id}/bridge/run",
        json={"command": command, "args": args},
    )
    r.raise_for_status()
    run = r.json()
    run_id = run["id"]
    final = stream_sse(
        client,
        f"/projects/{project_id}/bridge/stream?{urlencode({'run_id': run_id})}",
        run_id,
        f"{command} {' '.join(args)}".strip(),
    )
    return final


def select_bootstrap_version(project_id: str, chapter: int, version: str) -> None:
    """bootstrap select N X — 直接 import 调 select_version(project_id)。
    走 subprocess 的话 CLI 不接受 --novel_id，memory 会落到默认 key。"""
    sys.path.insert(0, str(NOVEL_AI_DIR))
    # 加载 novel_AI/.env（select_version 间接依赖 env 里的 API key，但 MVP
    # 阶段只要 .env 存在就 OK；真的用 memory 还要有真 key）
    env_file = NOVEL_AI_DIR / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())

    from tools.bootstrap import select_version
    print(f"\n{'='*60}\n[bootstrap select {chapter} {version}] novel_id={project_id}\n{'='*60}")
    select_version(chapter, version, novel_id=project_id)


def main() -> int:
    parser = argparse.ArgumentParser(description="一键跑 novel_AI MVP 流水线")
    parser.add_argument("project_id", help="目标 project_id（先在 frontend 新建项目）")
    parser.add_argument("--api", default="http://localhost:8123", help="后端 base URL")
    parser.add_argument("--chapters", type=int, default=1, help="run 命令写多少章（默认 1）")
    parser.add_argument("--select", default="A", help="bootstrap 选哪版（A/B/C）")
    parser.add_argument("--skip-bootstrap", action="store_true", help="跳过 bootstrap 步骤（如果之前跑过）")
    args = parser.parse_args()

    started = time.time()
    summary = {"steps": []}

    with httpx.Client(base_url=args.api, timeout=600.0) as client:
        # 0. health check
        try:
            r = client.get("/health")
            r.raise_for_status()
        except Exception as e:
            print(f"✗ 后端 {args.api} 不可达：{e}\n  请先在另一个终端跑: cd backend && uvicorn app.main:app --reload --port 8123")
            return 1

        # 1. push-concept
        try:
            r = client.post(f"/projects/{args.project_id}/bridge/push-concept")
            r.raise_for_status()
            print(f"\n[1/7] push-concept OK")
            summary["steps"].append({"step": "push-concept", "ok": True})
        except httpx.HTTPStatusError as e:
            print(f"✗ push-concept 失败: {e.response.status_code} {e.response.text}")
            print("  提示: 必须先在 frontend 完成 worldbuild (10 阶段)")
            return 1

        # 2. planner
        final = call_bridge_run(client, args.project_id, "planner", [])
        if final.get("exit_code") != 0:
            print(f"✗ planner 失败（exit_code={final.get('exit_code')}）")
            return 1
        summary["steps"].append({"step": "planner", "exit_code": 0})

        # 3. pull-setting（auto 在 planner 完成后已触发，等一秒让回灌完成）
        time.sleep(2)
        try:
            r = client.post(f"/projects/{args.project_id}/bridge/pull-setting")
            r.raise_for_status()
            print(f"\n[3/7] pull-setting OK")
            summary["steps"].append({"step": "pull-setting", "ok": True})
        except Exception as e:
            print(f"⚠ pull-setting 失败: {e}")

        # 4. bootstrap
        if not args.skip_bootstrap:
            final = call_bridge_run(client, args.project_id, "bootstrap", [])
            if final.get("exit_code") != 0:
                print(f"✗ bootstrap 失败（exit_code={final.get('exit_code')}）")
                return 1
            summary["steps"].append({"step": "bootstrap", "exit_code": 0})

            # 5. select
            try:
                select_bootstrap_version(args.project_id, 1, args.select)
                summary["steps"].append({"step": f"select 1 {args.select}", "ok": True})
            except Exception as e:
                print(f"✗ select 失败: {e}")
                return 1
        else:
            print(f"\n[4-5/7] bootstrap + select (skipped via --skip-bootstrap)")

        # 6. run N
        final = call_bridge_run(client, args.project_id, "run", [str(args.chapters)])
        if final.get("exit_code") != 0:
            print(f"✗ run 失败（exit_code={final.get('exit_code')}）")
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
    print(f"\n{'='*60}\nMVP 跑完！总耗时 {elapsed:.1f}s\n{'='*60}")
    print(json.dumps(summary, ensure_ascii=False, indent=2))

    # 检查 chapters 落盘
    chapters_dir = NOVEL_AI_DIR / "output" / "chapters"
    ch_files = sorted(chapters_dir.glob("ch_*.txt")) if chapters_dir.exists() else []
    print(f"\nnovel_AI/output/chapters/ 共 {len(ch_files)} 个章节文件")
    for f in ch_files[-3:]:
        size = f.stat().st_size
        print(f"  {f.name}  ({size} bytes)")

    return 0


if __name__ == "__main__":
    sys.exit(main())
