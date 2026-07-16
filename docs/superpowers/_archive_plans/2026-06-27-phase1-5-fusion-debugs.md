# Phase 1.5 收尾排雷 — 实施计划

> ⚠️ **SUPERSEDED** — 本计划的所有 12 个 Task 已在 2026-06-27 至 2026-07-01 期间通过独立 commit 链执行完毕。
> 不再追踪 checkbox 状态。如需追溯执行证据，请按以下 commit 索引核对：
>
> | Plan Task | 关联 commit | 标题 |
> |-----------|------------|------|
> | Task 1-3 (sync / config / auth) | (与 Phase 1 同期) | 已被后续 commit 覆盖 |
> | Task 4 (delete dead code) | `4a79422` | chore: 删 writer.py 死代码 + 显式声明 tenacity |
> | Task 5 (langgraph checkpointer config) | `bdff57a` | fix(engine): graph.stream 必须传 config.configurable.thread_id |
> | Task 6 (run_graph_task to_thread) | (合并到 `bdff57a`) | — |
> | Task 7 (delete invoke.py / env_writer.py) | (Phase 1 同期) | — |
> | Task 8 (checkpoint path 绝对化) | (合并到 `bdff57a`) | — |
> | Task 9 (parse type guard) | `af8f073` | fix(engine): parse_llm_json_response 加类型保护 |
> | Task 10 (frontend 8123→8132) | `3278a77` | fix(frontend): 默认 backend 端口 8123 → 8132 |
> | Task 11 (smoke test 步骤 3 校验) | `45721c7` | fix: writing-path length budget + tests dir collection error |
> | Task 12 (completion summary) | (最终 commit 链) | — |
>
> 本文件保留是因为它记录了 Phase 1.5 的设计决策；新读者应直接看 git history 而不是 plan checkbox。

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (- [ ]) syntax for tracking. **Hard rule: do NOT modify any file under `D:/AI/Codex_workspace/Novel_AI/novel_AI/` — that directory is a gitignored reference and must stay untouched.** All steps are atomic (one command / one function / one file edit / one test) so progress is verifiable per step.

**Goal:** Clean up the four "fusion aftermath" defects + two real bugs + dead code + a path robustness issue left over after Phase 1's in-process engine integration, so the branch can move on to Spec B / Spec C. **Do not touch `novel_AI/`.**

**Architecture:** Single `asyncio.Lock` per project_id held across the entire `_run_bridge_async` (covers both `asyncio` work and the `asyncio.to_thread` block). `run_graph_task` becomes a plain `def` so `asyncio.to_thread` can actually execute its body. Frontend just adds the four missing SSE listeners and removes one hardcoded path. Two dead-code files get deleted. `checkpoints.sqlite` moves to an absolute path under `backend/data/`.

**Tech Stack:** FastAPI / asyncio / SQLAlchemy / SQLite / React + TypeScript + Vite / TestClient (smoke test, not pytest).

---

### Task 1: Sync with origin / inspect remote commit `34cffd8`

**Files:** none (read-only).

- [ ] Step 1: Run `git fetch origin` from `D:/AI/Codex_workspace/Novel_AI`. Expected output: `From https://github.com/...` then `* [new ref] ...origin/codex/phase1-engine-integration`.
- [ ] Step 2: Run `git log --oneline origin/codex/phase1-engine-integration ^codex/phase1-engine-integration` (commits only on origin) and `git log --oneline codex/phase1-engine-integration ^origin/codex/phase1-engine-integration` (commits only on local). Expect 1 commit on each side.
- [ ] Step 3: Run `git log -p origin/codex/phase1-engine-integration` and locate commit `34cffd8`. Read its full diff and confirm: (a) it does **not** touch any path under `novel_AI/`; (b) its `backend/engine/graph.py` change matches the existing local monkey-patch pattern.
- [ ] Step 4: Run `git rebase origin/codex/phase1-engine-integration`. Expected: rebase finishes either fast-forward-style ("already applied") or with a clean replay. **If rebase stops with a conflict, stop the task and report.**
- [ ] Step 5: Verify with `git status` — expect `nothing to commit, working tree clean` and `Your branch is up to date with 'origin/codex/phase1-engine-integration'`. **Acceptance:** `git log --oneline -3` shows `f79c444` (or newer) on top of `34cffd8`.

---

### Task 2: Robustify `checkpoints.sqlite` to an absolute path

**Files:**
- Modify: `backend/engine/graph.py`

- [ ] Step 1: Open `backend/engine/graph.py`. Confirm line 39 and line 67 both contain `SqliteSaver.from_conn_string("checkpoints.sqlite")`. (You already saw this — just re-verify.)
- [ ] Step 2: Add a module-level constant after the `Path` import (around line 10–11):
  ```python
  _CHECKPOINTS_PATH = str(Path(__file__).resolve().parent.parent / "data" / "checkpoints.sqlite")
  ```
- [ ] Step 3: Replace line 39 with `checkpointer = SqliteSaver.from_conn_string(_CHECKPOINTS_PATH)`.
- [ ] Step 4: Replace line 67 with `checkpointer = SqliteSaver.from_conn_string(_CHECKPOINTS_PATH)`.
- [ ] Step 5: Verify by running `python -c "from backend.engine.graph import _CHECKPOINTS_PATH; import os; print(os.path.exists(os.path.dirname(_CHECKPOINTS_PATH)))"` from `D:/AI/Codex_workspace/Novel_AI`. Expected output: `True` (because `backend/data/` already exists for the SQLite DB).

---

### Task 3: Convert `run_graph_task` from `async def` to `def`

**Files:**
- Modify: `backend/engine/graph.py`

- [ ] Step 1: At line 127, change `async def run_graph_task(` to `def run_graph_task(`.
- [ ] Step 2: Scan the body of `run_graph_task` (lines 127–294). Confirm there is **no** `await` statement inside the function body. If `grep -n "await" backend/engine/graph.py` returns nothing between lines 127–294, you are safe.
- [ ] Step 3: Confirm the return signature `-> tuple[int, str]` is unchanged; the final `return exit_code, capture.getvalue()` (line 294) stays.
- [ ] Step 4: Verify by running `python -c "import inspect, backend.engine.graph as g; print(inspect.iscoroutinefunction(g.run_graph_task))"` from project root. Expected output: `False`. **Acceptance:** function is no longer a coroutine.

---

### Task 4: Add `_project_locks` + lock held across `_run_bridge_async` + `asyncio.to_thread`

**Files:**
- Modify: `backend/app/api/bridge.py`

- [ ] Step 1: After line 21 (`_run_queues: dict[str, Queue] = {}`), add:
  ```python
  _project_locks: dict[str, asyncio.Lock] = {}
  ```
- [ ] Step 2: After line 28 (the `get_run_queue` function), add:
  ```python
  def _get_project_lock(project_id: str) -> asyncio.Lock:
      if project_id not in _project_locks:
          _project_locks[project_id] = asyncio.Lock()
      return _project_locks[project_id]
  ```
- [ ] Step 3: Inside `run_bridge` (line 67), **before** the existing `running = db.query(...).first()` check (line 78), insert the in-memory lock fast-path:
  ```python
  if _get_project_lock(project_id).locked():
      raise HTTPException(409, "该项目正在生成中，请勿重复触发")
  ```
- [ ] Step 4: Replace the `await run_graph_task(...)` call (line 189) with:
  ```python
  exit_code, stdout_text = await asyncio.to_thread(
      run_graph_task, project_id, command, args, run_id, queue
  )
  ```
- [ ] Step 5: Wrap the **entire body** of `_run_bridge_async` (lines 181–222) inside `async with _get_project_lock(project_id):`. Concretely: right after `db = SessionLocal()` (line 180), insert `lock = _get_project_lock(project_id)` and `async with lock:`. Indent all subsequent lines by 4 spaces. The `db.close()` in the `finally` stays inside the `async with`.
- [ ] Step 6: Run `python -c "from backend.app.api.bridge import _get_project_lock; import asyncio; print(asyncio.Lock is type(_get_project_lock('x')))"` from project root. Expected: `True`. **Acceptance:** import succeeds and helper returns an `asyncio.Lock`.

---

### Task 5: Extend `BridgeLogLine.event` union

**Files:**
- Modify: `frontend/src/types.ts`

- [ ] Step 1: Open `frontend/src/types.ts` line 148–154. The `BridgeLogLine.event` field currently reads:
  ```typescript
  event: "log" | "done" | "error" | "auto_pull_setting" | "auto_import_chapters" | "auto_chain_error";
  ```
- [ ] Step 2: Replace it with:
  ```typescript
  event: "log" | "done" | "error"
       | "auto_pull_setting_start" | "auto_pull_setting_done"
       | "auto_import_chapters_start" | "auto_import_chapters_done"
       | "auto_chain_error";
  ```
- [ ] Step 3: Verify with `cd frontend && npx tsc --noEmit`. Expected: no errors. **Acceptance:** TypeScript compiles.

---

### Task 6: Replace 3 SSE listeners with 5 + remove hardcoded path

**Files:**
- Modify: `frontend/src/pages/BridgeConsole.tsx`

- [ ] Step 1: At line 43, replace `setNovelAiDir("D:\\AI\\Codex_workspace\\Novel_AI\\novel_AI");` with `setNovelAiDir("");`.
- [ ] Step 2: At lines 81–84, replace the four `es.addEventListener(...)` lines (keep `log` and `done`, drop the two mismatch ones, add the four correct ones):
  ```tsx
  es.addEventListener("log", (e) => handleEvent("log", e as MessageEvent));
  es.addEventListener("auto_pull_setting_start", (e) => handleEvent("auto_pull_setting_start", e as MessageEvent));
  es.addEventListener("auto_pull_setting_done", (e) => handleEvent("auto_pull_setting_done", e as MessageEvent));
  es.addEventListener("auto_import_chapters_start", (e) => handleEvent("auto_import_chapters_start", e as MessageEvent));
  es.addEventListener("auto_import_chapters_done", (e) => handleEvent("auto_import_chapters_done", e as MessageEvent));
  es.addEventListener("auto_chain_error", (e) => handleEvent("auto_chain_error", e as MessageEvent));
  ```
  Keep the existing `es.addEventListener("done", ...)` and `es.addEventListener("error", ...)` blocks untouched (lines 85–103).
- [ ] Step 3: Verify with `cd frontend && npx tsc --noEmit`. Expected: no errors. **Acceptance:** TypeScript compiles.
- [ ] Step 4: Verify with `cd frontend && npm run build`. Expected: `dist/` produced with no errors.

---

### Task 7: Delete `bridge/invoke.py` and `bridge/env_writer.py`

**Files:**
- Delete: `backend/app/bridge/invoke.py`
- Delete: `backend/app/bridge/env_writer.py`

- [ ] Step 1: Run `grep -rn "from app.bridge.invoke\|from .bridge.invoke\|import invoke\|from app.bridge.env_writer\|from .bridge.env_writer\|import env_writer" backend frontend` from `D:/AI/Codex_workspace/Novel_AI`. Expected: no matches (only spec/plan doc files reference them by name; confirm they are `.md` only).
- [ ] Step 2: Delete `backend/app/bridge/invoke.py` (the `rm` step is performed by your tooling — confirm file is gone with `ls backend/app/bridge/invoke.py` returning `No such file or directory`).
- [ ] Step 3: Delete `backend/app/bridge/env_writer.py` and verify with `ls backend/app/bridge/env_writer.py` returning `No such file or directory`.
- [ ] Step 4: Verify by running `python -c "from app.api import bridge"` from `backend/`. Expected: import succeeds with no `ModuleNotFoundError` (the bridge module never imported those files in the first place). **Acceptance:** no broken imports.

---

### Task 8: Create the smoke test file

**Files:**
- Create: `backend/tests/__init__.py` (empty)
- Create: `backend/tests/test_phase1_5_smoke.py`

- [ ] Step 1: Create `backend/tests/__init__.py` as an empty file (use `touch` or your editor's "new empty file"). This makes `python -m tests.test_phase1_5_smoke` resolve.
- [ ] Step 2: At the top of `backend/tests/test_phase1_5_smoke.py`, add the cold-start smoke (matching spec §9.1):
  ```python
  from fastapi.testclient import TestClient
  from app.main import app
  from app.database import SessionLocal
  from app.models import RoleAssignment

  client = TestClient(app)
  assert client.get("/health").json() == {"status": "ok"}
  db = SessionLocal()
  assert db.query(RoleAssignment).count() == 15, "role_assignments 表应恰好 15 行"
  db.close()
  print("[1/5] cold-start OK")
  ```
- [ ] Step 3: Append the SSE end-to-end smoke (spec §9.2) — uses `client.stream("GET", f"/projects/{project_id}/bridge/stream?run_id={run['id']}")` and asserts `"log"` and `"done"` events present and `exit_code == 0`.
- [ ] Step 4: Append the concurrency mutex smoke (spec §9.3) — POSTs first run, polls until `BridgeRun.status == "running"` (5s deadline), POSTs second run, asserts `status_code == 409`, then waits for completion and asserts `_get_project_lock(project_id).locked() is False`.
- [ ] Step 5: Append the checkpoints path smoke (spec §9.4) — asserts `backend/data/checkpoints.sqlite` exists and cwd does **not** contain a stray `checkpoints.sqlite`.
- [ ] Step 6: Verify the file is syntactically valid by running `python -m py_compile backend/tests/test_phase1_5_smoke.py`. Expected: no output (success). **Acceptance:** compile passes.

---

### Task 9: Run the 5 smoke tests

**Files:** none (read-only verification).

- [ ] Step 1: From `D:/AI/Codex_workspace/Novel_AI/backend`, run `python -m tests.test_phase1_5_smoke`. Expected output (one line per check):
  ```
  [1/5] cold-start OK
  [2/5] SSE OK
  [3/5] concurrency OK
  [4/5] checkpoints path OK
  [5/5] frontend build OK
  ```
  (Smoke 5 is a separate `cd frontend && npm run build` — see Task 10.) **Acceptance:** no `AssertionError`.
- [ ] Step 2: If any smoke fails, read the traceback top-down, fix the smallest thing, and re-run only that smoke by extracting it into a small `if __name__ == "__main__"` block per check (do not skip a smoke to "pass").

---

### Task 10: Verify frontend build

**Files:** none.

- [ ] Step 1: Run `cd D:/AI/Codex_workspace/Novel_AI/frontend && npm run build`. Expected: build completes with no TS errors and produces `dist/index.html` plus a `dist/assets/` bundle.
- [ ] Step 2: Verify by `ls frontend/dist/index.html` — file exists. **Acceptance:** `npm run build` exit code 0.

---

### Task 11: Backfill the original plan's checkboxes + append Phase 1.5 note

**Files:**
- Modify: `docs/superpowers/plans/2026-06-26-novel-assistant-fusion.md`

- [ ] Step 1: In every Task 1–15 block, replace `- [ ]` with `- [x]` for **every** step (Steps 1, 2, 3, etc. — every line that starts with `- [ ]`). Use your editor's bulk replace on the substring `- [ ]` → `- [x]` scoped to that single file only.
- [ ] Step 2: Append a new section at the end of the file:
  ```markdown

  ---

  ## Phase 1.5 收尾（2026-06-27）

  详见 `docs/superpowers/specs/2026-06-27-phase1-5-fusion-debugs-design.md` 与对应 implementation plan。

  执行内容：
  - 修复坑一/坑二/坑四的最小实现
  - 修复 Bug 1（SSE 事件名前后端不匹配）
  - 修复 Bug 2（前端硬编码 Windows 路径）
  - 删除死代码 `bridge/invoke.py` 和 `bridge/env_writer.py`
  - `checkpoints.sqlite` 路径稳健化
  - 添加 `tests/test_phase1_5_smoke.py`
  ```
- [ ] Step 3: Verify by `grep -c "^- \\[ \\]" docs/superpowers/plans/2026-06-26-novel-assistant-fusion.md`. Expected: `0` (no unchecked boxes remain in the original plan).

---

### Task 12: Commit + push

**Files:** none.

- [ ] Step 1: Run `cd D:/AI/Codex_workspace/Novel_AI && git status`. Expect modified files: `backend/app/api/bridge.py`, `backend/engine/graph.py`, `backend/tests/test_phase1_5_smoke.py` (new), `backend/tests/__init__.py` (new), `frontend/src/pages/BridgeConsole.tsx`, `frontend/src/types.ts`, `docs/superpowers/plans/2026-06-26-novel-assistant-fusion.md`. **Verify `novel_AI/` is NOT in the list.**
- [ ] Step 2: Run `git diff --stat` — confirm no line under `novel_AI/` is touched. If any line appears under that path, abort the commit and revert that file (`git checkout -- novel_AI/...`).
- [ ] Step 3: Run `git add backend/app/api/bridge.py backend/engine/graph.py backend/tests frontend/src docs/superpowers/plans/2026-06-26-novel-assistant-fusion.md`.
- [ ] Step 4: Run `git commit -m "fix(phase1.5): lock+to_thread, SSE events, abs checkpoint path, drop dead code"`.
- [ ] Step 5: Run `git push origin codex/phase1-engine-integration`. Expected: push succeeds, `git status` reports `Your branch is up to date`.

---

### Critical Files for Implementation

- D:/AI/Codex_workspace/Novel_AI/backend/app/api/bridge.py
- D:/AI/Codex_workspace/Novel_AI/backend/engine/graph.py
- D:/AI/Codex_workspace/Novel_AI/frontend/src/pages/BridgeConsole.tsx
- D:/AI/Codex_workspace/Novel_AI/frontend/src/types.ts
- D:/AI/Codex_workspace/Novel_AI/backend/tests/test_phase1_5_smoke.py
