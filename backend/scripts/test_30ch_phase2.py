"""30-chapter real LLM test — Phase 2: bridge pipeline (real LLM).

读取 preset worldbuild 留下的 project_id，依次跑：
  push-concept → planner → pull-setting → bootstrap → select A →
  run 30 → import-chapters → 一致性核查。
"""
import json
import os
import sys
import time
from pathlib import Path

import httpx

BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

API = os.environ.get("NOVEL_API", "http://127.0.0.1:8132")
RUN_DIR = Path(os.environ.get(
    "NOVEL_RUN_DIR",
    "D:/AI/Codex_workspace/Novel_AI/docs/runs/30ch-real-2026-07-22",
))
RUN_DIR.mkdir(parents=True, exist_ok=True)
LOG_FILE = RUN_DIR / "phase2.log"
SUMMARY_FILE = RUN_DIR / "summary_phase2.json"


def log(msg, **kw):
    ts = time.strftime("%H:%M:%S")
    extra = " ".join(f"{k}={v}" for k, v in kw.items())
    line = f"[{ts}] {msg} {extra}"
    print(line, flush=True)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def stream_sse(url, label, timeout=3600):
    log(f"GET {url[:90]}...")
    lines = []
    start = time.time()
    try:
        with httpx.stream("GET", url, timeout=timeout) as r:
            for line in r.iter_lines():
                if line:
                    lines.append(line)
        log(f"  → {label} done, lines={len(lines)} elapsed={time.time()-start:.0f}s")
    except Exception as e:
        log(f"  → {label} ERROR: {type(e).__name__}: {e}")
    return lines


def post_run_sync(project_id, command, args=None, label="", timeout=3600):
    body = {"command": command, "args": args or []}
    log(f"POST /bridge/run command={command} {label}")
    r = httpx.post(f"{API}/projects/{project_id}/bridge/run", json=body, timeout=30)
    if r.status_code != 200:
        log(f"  ✗ {r.status_code} {r.text[:300]}")
        return None
    bridge = r.json()
    run_id = bridge["id"]
    log(f"  run_id={run_id}")
    sse_url = f"{API}/projects/{project_id}/bridge/stream?run_id={run_id}"
    lines = stream_sse(sse_url, f"{command} {label}", timeout=timeout)
    return {"run_id": run_id, "bridge": bridge, "lines": lines}


def main():
    if LOG_FILE.exists():
        LOG_FILE.unlink()

    pid = open(RUN_DIR / "pid.txt").read().strip()
    log(f"using project_id={pid}")

    summary = {"project_id": pid}

    # 绑定 engine
    log("STEP 0 bind engine output dir")
    engine_dir = RUN_DIR / "engine"
    engine_dir.mkdir(parents=True, exist_ok=True)
    r = httpx.put(f"{API}/projects/{pid}/bridge/binding", json={
        "novel_ai_dir": str(engine_dir),
        "novel_id": pid,
    }, timeout=30)
    r.raise_for_status()
    log(f"  bound to {engine_dir}")

    # audit_mode=lite 真实质控（保留主链 + 关键质控，跳过最贵的 3 模型交叉，
    # 控制 Token Plan 用量以保 30 章跑完）
    log("STEP 0.5 set audit_mode=lite")
    r = httpx.post(f"{API}/projects/{pid}/bridge/set-audit-mode", json={"mode": "lite"}, timeout=10)
    log(f"  → {r.status_code} {r.text[:120]}")

    # push-concept
    log("STEP 1 push-concept (real LLM via minimax)")
    t0 = time.time()
    r = httpx.post(f"{API}/projects/{pid}/bridge/push-concept", timeout=60)
    summary["push_concept"] = {"status": r.status_code, "body": r.text[:300]}
    log(f"  → {r.status_code} {r.text[:200]}")
    summary["push_concept_seconds"] = round(time.time() - t0, 1)

    # planner
    log("STEP 2 planner (real LLM)")
    t0 = time.time()
    planner_res = post_run_sync(pid, "planner", label="planner")
    summary["planner"] = planner_res["bridge"] if planner_res else None
    summary["planner_seconds"] = round(time.time() - t0, 1)
    with open(RUN_DIR / "planner_sse.jsonl", "w", encoding="utf-8") as f:
        for line in (planner_res["lines"] if planner_res else []):
            f.write(line + "\n")

    # pull-setting
    log("STEP 3 pull-setting")
    t0 = time.time()
    r = httpx.post(f"{API}/projects/{pid}/bridge/pull-setting", timeout=60)
    summary["pull_setting"] = {"status": r.status_code, "body": r.text[:300]}
    log(f"  → {r.status_code} {r.text[:200]}")
    summary["pull_setting_seconds"] = round(time.time() - t0, 1)

    # bootstrap
    log("STEP 4 bootstrap (real LLM, 3 candidates)")
    t0 = time.time()
    bootstrap_res = post_run_sync(pid, "bootstrap", label="bootstrap")
    summary["bootstrap"] = bootstrap_res["bridge"] if bootstrap_res else None
    summary["bootstrap_seconds"] = round(time.time() - t0, 1)
    with open(RUN_DIR / "bootstrap_sse.jsonl", "w", encoding="utf-8") as f:
        for line in (bootstrap_res["lines"] if bootstrap_res else []):
            f.write(line + "\n")

    # select A
    log("STEP 5 select A (chapter 1) via engine direct")
    os.environ["NOVEL_AI_DIR"] = str(engine_dir)
    from engine.tools.bootstrap import select_version
    try:
        select_version(1, "A", novel_id=pid)
        summary["select"] = "ok"
    except Exception as e:
        summary["select"] = f"failed: {e}"
        log(f"  ✗ select failed: {e}")

    # init_arc — 生成弧任务单（run 30 的前置）
    log("STEP 5.5 init_arc (生成弧任务单)")
    t0 = time.time()
    init_arc_res = post_run_sync(pid, "init_arc", label="init_arc", timeout=1200)
    summary["init_arc"] = init_arc_res["bridge"] if init_arc_res else None
    summary["init_arc_seconds"] = round(time.time() - t0, 1)
    with open(RUN_DIR / "init_arc_sse.jsonl", "w", encoding="utf-8") as f:
        for line in (init_arc_res["lines"] if init_arc_res else []):
            f.write(line + "\n")

    # run 30 chapters
    log("STEP 6 run 30 chapters (real LLM, full audit)")
    t0 = time.time()
    run_res = post_run_sync(pid, "run", args=["30"], label="write 30 chapters", timeout=21600)
    summary["run"] = run_res["bridge"] if run_res else None
    summary["run_seconds"] = round(time.time() - t0, 1)
    with open(RUN_DIR / "run30_sse.jsonl", "w", encoding="utf-8") as f:
        for line in (run_res["lines"] if run_res else []):
            f.write(line + "\n")

    # import
    log("STEP 7 import-chapters")
    t0 = time.time()
    r = httpx.post(f"{API}/projects/{pid}/bridge/import-chapters", json={}, timeout=120)
    summary["import_chapters"] = {"status": r.status_code, "body": r.text[:500]}
    log(f"  → {r.status_code} {r.text[:300]}")
    summary["import_chapters_seconds"] = round(time.time() - t0, 1)

    # 校验
    log("STEP 8 verify")
    r = httpx.get(f"{API}/projects/{pid}/chapters", timeout=10)
    chapters = r.json()
    summary["chapters_total"] = len(chapters)
    log(f"  total chapters in DB: {len(chapters)}")
    for c in chapters[:35]:
        log(f"    ch{c['chapter_no']:>2} {c.get('title','')[:30]:30s} preview={len(c.get('content_preview') or '')} chars")

    # engine artifacts
    log(f"engine output dir: {engine_dir}")
    for p in sorted(engine_dir.rglob("*")):
        if p.is_file() and p.stat().st_size < 500_000:
            log(f"  {p.relative_to(engine_dir)} ({p.stat().st_size}B)")

    with open(SUMMARY_FILE, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2, default=str)
    log(f"summary saved → {SUMMARY_FILE}")
    log("DONE")


if __name__ == "__main__":
    main()
