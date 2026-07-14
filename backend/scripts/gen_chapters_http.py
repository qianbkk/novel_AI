"""HTTP 模式批量章节生成（P5 验证用）

通过 bridge HTTP API 走完整 MVP 流水线：
1. POST /projects/{id}              建项目
2. POST /bridge/run command=push-concept   推 novel_config.json
3. POST /bridge/run command=planner       生成 setting_package.json
4. POST /bridge/run command=pull-setting  拉回 DB
5. POST /bridge/run command=bootstrap     黄金三章 A/B/C
6. POST /bridge/run command=select 1 A    选第 1 章版本 A
7. POST /bridge/run command=run N         跑 N 章

前置：
    - uvicorn 后端跑在 8132（dev.bat start-backend）
    - ANTHROPIC_BASE_URL=https://api.minimaxi.com/anthropic
    - ANTHROPIC_AUTH_TOKEN=MiniMax token

用法：
    cd backend
    python -m scripts.gen_chapters_http \\
        --project-id gen_300_<timestamp> \\
        --title "测试长篇" --genre "玄幻" \\
        --concept "少年觉醒，闯荡修真世界" \\
        --chapters 50
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import uuid
from pathlib import Path

import httpx

BACKEND_URL = "http://127.0.0.1:8132"
BACKEND_ROOT = Path(__file__).resolve().parents[1]


def post_project(client: httpx.Client, project_id: str, title: str, genre: str) -> None:
    """建项目（如果已存在就忽略 409）。"""
    r = client.post(
        "/projects",
        json={
            "id": project_id,
            "title": title,
            "genre": genre,
            "config_json": {},
        },
    )
    if r.status_code in (200, 201, 409):
        print(f"  ✓ project {project_id} ok ({r.status_code})")
        return
    raise RuntimeError(f"建项目失败：{r.status_code} {r.text[:200]}")


def put_binding(client: httpx.Client, project_id: str, novel_ai_dir: str) -> None:
    """PUT /projects/{id}/bridge/binding — bridge 要求有 binding 才能跑命令。"""
    r = client.put(
        f"/projects/{project_id}/bridge/binding",
        json={"novel_ai_dir": novel_ai_dir},
    )
    if r.status_code in (200, 201):
        print(f"  ✓ binding {novel_ai_dir} ok")
        return
    raise RuntimeError(f"绑定失败：{r.status_code} {r.text[:200]}")


def bridge_run(client: httpx.Client, project_id: str, command: str, args: list[str], label: str) -> dict:
    """POST /bridge/run + 同步等 done 事件。"""
    r = client.post(
        f"/projects/{project_id}/bridge/run",
        json={"command": command, "args": args},
    )
    r.raise_for_status()
    run = r.json()
    run_id = run["id"]
    print(f"\n{'='*60}\n[{label}] {command} {' '.join(args)}\n{'='*60}")

    final = {}
    last_log_count = 0
    with client.stream("GET", f"/projects/{project_id}/bridge/stream?run_id={run_id}") as resp:
        for line in resp.iter_lines():
            if not line.startswith("data: "):
                continue
            try:
                ev = json.loads(line[6:])
            except json.JSONDecodeError:
                continue
            et = ev.get("event", "?")
            if et == "log":
                msg = ev.get("line", "").rstrip()
                if msg:
                    print(f"  {msg}")
                last_log_count += 1
            elif et == "node_start":
                print(f"  → {ev.get('node', '?')}")
            elif et == "node_end":
                print(f"  ← {ev.get('node', '?')}")
            elif et == "auto_import_chapters_done":
                imported = ev.get("imported", [])
                print(f"  ✓ auto import-chapters done ({len(imported)} chapters)")
            elif et == "done":
                final = ev
            elif et == "error":
                print(f"  ✗ ERROR: {ev.get('message', '')}")
    print(f"[{label}] exit_code={final.get('exit_code','?')}")
    return final


def main() -> int:
    parser = argparse.ArgumentParser(description="HTTP 批量生成章节（P5 验证）")
    parser.add_argument("--project-id", default=None,
                        help="项目 ID（默认 gen_<timestamp>_<random>）")
    parser.add_argument("--title", default="测试长篇")
    parser.add_argument("--genre", default="玄幻")
    parser.add_argument("--platform", default="fanqie")
    parser.add_argument("--concept", default="少年觉醒，闯荡修真世界")
    parser.add_argument("--chapters", type=int, default=50)
    parser.add_argument("--api", default=BACKEND_URL)
    parser.add_argument("--audit-mode", default="lite",
                        choices=["full", "lite", "bootstrap", "draft"])
    parser.add_argument("--skip-bootstrap", action="store_true",
                        help="跳过 bootstrap+select（适用于已初始化过的项目）")
    args = parser.parse_args()

    project_id = args.project_id or f"gen_{int(time.time())}_{uuid.uuid4().hex[:6]}"
    print(f"📖 批量生成：{args.title}")
    print(f"   project_id = {project_id}")
    print(f"   目标章数 = {args.chapters}")
    print(f"   API = {args.api}")
    print(f"   audit_mode = {args.audit_mode}")

    # 通过 env 注入 audit_mode（bridge 启动时读）
    os.environ["NOVEL_AUDIT_MODE"] = args.audit_mode

    started_total = time.time()
    with httpx.Client(base_url=args.api, timeout=600) as client:
        # 0. health check
        try:
            client.get("/health").raise_for_status()
        except Exception as e:
            print(f"❌ backend 不在 {args.api}，无法继续：{e}")
            return 1

        # 1. 建项目
        post_project(client, project_id, args.title, args.genre)

        # 1b. 绑定 NovelAIBinding（bridge 要求有 binding 才能跑命令）
        # 默认绑到仓库根目录的 novel_AI（dev.bat 默认产物位置）
        novel_ai_dir = str(BACKEND_ROOT.parent / "novel_AI")
        put_binding(client, project_id, novel_ai_dir)

        # 2. push-concept（写 novel_config.json）
        bridge_run(client, project_id, "push-concept", [
            "--title", args.title,
            "--genre", args.genre,
            "--platform", args.platform,
            "--concept", args.concept,
        ], label="push-concept")

        # 3. planner（生成 setting_package.json）
        bridge_run(client, project_id, "planner", [], label="planner")

        # 4. pull-setting（拉回 DB）
        bridge_run(client, project_id, "pull-setting", [], label="pull-setting")

        if not args.skip_bootstrap:
            # 5. bootstrap（黄金三章 A/B/C）
            bridge_run(client, project_id, "bootstrap", [], label="bootstrap")

            # 6. select 第 1 章版本 A
            bridge_run(client, project_id, "select", ["1", "A"], label="select 1 A")

        # 7. 跑 N 章
        final = bridge_run(client, project_id, "run", [str(args.chapters)], label=f"run {args.chapters}")

    elapsed = time.time() - started_total
    print(f"\n{'='*60}")
    print(f"📊 生成完成")
    print(f"   总耗时: {elapsed:.1f}s")
    print(f"   exit_code: {final.get('exit_code', '?')}")
    print(f"   project_id: {project_id}")
    print(f"{'='*60}")

    # 跨存储对账（审计 P2）
    print("\n[跨存储对账]")
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from scripts.reconcile_storage import reconcile
    rc = reconcile(project_id=project_id, novel_id=project_id, strict=False)
    print(f"   对账退出码: {rc}")
    return 0


if __name__ == "__main__":
    sys.exit(main())