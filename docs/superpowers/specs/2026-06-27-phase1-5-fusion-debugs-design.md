# Phase 1.5 收尾排雷 — 设计文档

> **Date:** 2026-06-27
> **Branch:** `codex/phase1-engine-integration`
> **Status:** Design approved, awaiting implementation plan
> **Scope:** Phase 1 原生集成（`backend/engine/` 进程内 LangGraph + SqliteSaver + DB 路由）的"融合后遗症"收尾

---

## 1. 目标

Phase 1 commit (`8338c98 Phase 1: 引擎原生集成 — 废弃子进程/改用 SqliteSaver/DB 路由`) 把 `novel_AI/` 引擎从子进程调起改成 FastAPI 进程内直接调 LangGraph。这个改造落地后还存在 4 个待修问题 + 2 个真 bug + 若干死代码/路径稳健化清理工作。

**本 spec 的目标**：把这些"融合后遗症"全部清掉，让 Phase 1 真正收尾，可以进 Spec B（Pydantic schema 落地）或 Spec C（UX 增强）。

**非目标**（明确划出去）：
- 不动 `novel_AI/` 任何文件（`CLAUDE.md` 已明确：`novel_AI/` 视为 gitignored reference）
- 不重写 agents / api_client / orchestrator（坑一的"换 AsyncAnthropic"路线被否决，scope 过大）
- 不接 `astream_events` 节点级事件（属于 Spec C 范畴）
- 不把 Pydantic schema 实际接进 StateGraph（属于 Spec B 范畴）
- 不写新 memory 代码（坑三是预防性，不存在 bug）

---

## 2. 改动面

只动 4 个文件 + 删 2 个文件 + 改 1 个计划文件：

| # | 文件 | 改动 | 关联问题 |
|---|---|---|---|
| 1 | `backend/app/api/bridge.py` | 加 per-project `asyncio.Lock` 字典；`_run_bridge_async` 用 `asyncio.to_thread` 包装 `run_graph_task` | 坑一、坑二 |
| 2 | `frontend/src/pages/BridgeConsole.tsx` | 3 个 `addEventListener` 改成 4 个，匹配后端 4 个 event 名；删第 43 行硬编码路径 | Bug 1、Bug 2 |
| 3 | `backend/engine/graph.py` | `SqliteSaver.from_conn_string("checkpoints.sqlite")` 改成绝对路径 | 路径稳健化（我新发现） |
| 4 | `backend/tests/test_phase1_5_smoke.py` | 新建：`TestClient` smoke test，验证并发锁 + SSE 事件 + checkpoint 路径 | 测试 |
| 5 | `backend/app/bridge/invoke.py` | **删除** | 死代码（0 调用点） |
| 6 | `backend/app/bridge/env_writer.py` | **删除** | 死代码（0 调用点） |
| 7 | `docs/superpowers/plans/2026-06-26-novel-assistant-fusion.md` | Task 1-15 全部从 `[ ]` 改 `[x]`，追加 Phase 1.5 收尾记录 | 记录同步 |

**不动的**：
- `engine/llm_router.py`（已在 `install()` 时改 `api_client` 模块全局，DB 路由 OK）
- `engine/schemas/graph_state.py`（Pydantic schema 没用上是 Spec B 的事）
- `engine/memory/`（空目录，留着）
- `novel_AI/` 下任何文件
- 任何 agent / tool / orchestrator 源码

---

## 3. 并发模型（修复坑一 + 坑二）

### 3.1 锁结构

`bridge.py` 顶层维护一个 `dict[str, asyncio.Lock]`：

```python
_project_locks: dict[str, asyncio.Lock] = {}

def _get_project_lock(project_id: str) -> asyncio.Lock:
    if project_id not in _project_locks:
        _project_locks[project_id] = asyncio.Lock()
    return _project_locks[project_id]
```

### 3.2 `POST /run` 流程改造

```python
@router.post("/run", response_model=BridgeRunOut)
async def run_bridge(
    project_id: str,
    payload: BridgeRunRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    # ... 既有 _get_project_and_binding / WRITE_COMMANDS 校验 ...
    
    # 新增：per-project 内存锁（快路径）
    lock = _get_project_lock(project_id)
    if lock.locked():
        raise HTTPException(409, "该项目正在生成中，请勿重复触发")
    
    # 既有：SQL 兜底（防 server 重启后 in-memory 锁清零）
    running = db.query(BridgeRun).filter_by(project_id=project_id, status="running").first()
    if running:
        raise HTTPException(409, "bridge run already running for this project")
    
    bridge_run = BridgeRun(...)
    db.add(bridge_run); db.commit(); db.refresh(bridge_run)
    
    queue = get_run_queue(bridge_run.id)
    
    # 新增：async with lock + asyncio.to_thread 包装
    async with lock:
        # _run_bridge_async 内部把同步 run_graph_task 丢进默认线程池
        asyncio.create_task(_run_bridge_async(bridge_run.id, project_id, command, payload.args or [], queue))
    
    return bridge_run
```

### 3.3 异步包装（修复坑一）

`run_graph_task` 本身保持 `async def`（签名不变），但**调用方**改成线程池调度：

```python
async def _run_bridge_async(run_id, project_id, command, args, queue):
    # ... 既有：db 启动 / queue.put({event:start}) ...
    try:
        # 改动点：把同步执行的 run_graph_task 丢进线程池
        exit_code, stdout_text = await asyncio.to_thread(
            run_graph_task, project_id, command, args, run_id, queue
        )
        # ... 后续既有：写 BridgeRun / auto-pull / auto-import / queue.put(done) ...
```

**为什么不用 `loop.run_in_executor(None, ...)`**：`asyncio.to_thread` 是 Python 3.9+ 的官方糖，等价于 `run_in_executor(None, ...)`，更简洁。

**为什么不需要 `threading.Lock`**：
- 引擎在 `asyncio.to_thread` 调度进线程池时，**每个 `run_graph_task` 调用独占一个线程**
- 同一 project_id 第二次进入 `POST /run` 时，已经被 `_project_locks` 拦在 409，**根本不会进入 `asyncio.to_thread`**
- 跨 project_id 的并发由 `asyncio.create_task` 自然调度进线程池，不冲突
- 引擎内部（`run_orchestrator` → `node_*`）全是顺序执行，无内部并发

### 3.4 双锁 vs 单锁取舍

审计 1 提的 `asyncio.Lock` 是必要的。
我之前草稿里提过 `asyncio.Lock + threading.Lock` 双锁——经回退分析，**单 `asyncio.Lock` 就够**（更 ponytail）。

---

## 4. SSE 事件名修复（修复 Bug 1）

### 4.1 现状

| 后端 `event` 字段（`bridge.py:200-210`） | 前端 `addEventListener`（`BridgeConsole.tsx:82-84`） | 是否触发 |
|---|---|---|
| `auto_pull_setting_start` | `auto_pull_setting` | ❌ |
| `auto_pull_setting_done` | （无） | ❌ |
| `auto_import_chapters_start` | `auto_import_chapters` | ❌ |
| `auto_import_chapters_done` | （无） | ❌ |
| `auto_chain_error` | `auto_chain_error` | ✅（但目前后端不发） |

`EventSource.addEventListener` **不支持前缀匹配**，4 个事件全部被丢弃，用户日志区只能看到 `[log]` → `[done]`。

### 4.2 修法

**改前端**，**不改后端**（后端逻辑零改动，前端多 1 行 listener 即可）：

```tsx
// BridgeConsole.tsx 替换现有 3 行 addEventListener
es.addEventListener("auto_pull_setting_start", (e) => handleEvent("auto_pull_setting_start", e as MessageEvent));
es.addEventListener("auto_pull_setting_done", (e) => handleEvent("auto_pull_setting_done", e as MessageEvent));
es.addEventListener("auto_import_chapters_start", (e) => handleEvent("auto_import_chapters_start", e as MessageEvent));
es.addEventListener("auto_import_chapters_done", (e) => handleEvent("auto_import_chapters_done", e as MessageEvent));
es.addEventListener("auto_chain_error", (e) => handleEvent("auto_chain_error", e as MessageEvent));
```

### 4.3 类型扩展

`frontend/src/types.ts::BridgeLogLine.event` 已支持这些字面量（`auto_pull_setting` / `auto_import_chapters` 等），但当前 union 是 `event: "log" | "done" | "error" | "auto_pull_setting" | "auto_import_chapters" | "auto_chain_error"`——**需要扩展**成 `_start` / `_done` 版本：

```typescript
export interface BridgeLogLine {
  event: "log" | "done" | "error" 
       | "auto_pull_setting_start" | "auto_pull_setting_done"
       | "auto_import_chapters_start" | "auto_import_chapters_done"
       | "auto_chain_error";
  // ... 其它字段不变 ...
}
```

---

## 5. 前端硬编码路径修复（修复 Bug 2）

`frontend/src/pages/BridgeConsole.tsx:43`：

```tsx
// 改前
.catch(() => {
  setNovelAiDir("D:\\AI\\Codex_workspace\\Novel_AI\\novel_AI");
});

// 改后
.catch(() => {
  setNovelAiDir("");
});
```

`""` 空串配合 `<input>` 的 `placeholder`（如果没填）已经能引导用户填入真实路径。

---

## 6. checkpoints.sqlite 路径稳健化

### 6.1 现状

`backend/engine/graph.py:39`：

```python
checkpointer = SqliteSaver.from_conn_string("checkpoints.sqlite")
```

裸字符串，文件落在 `cwd` 下。uvicorn 启动时 cwd 是 backend 根目录，**实际能用**——但任何"换个 cwd 跑测试"、"在 tests 目录跑 pytest"都会让 checkpoints 落到错误位置。

### 6.2 修法

```python
# 顶部
from pathlib import Path
_CHECKPOINTS_PATH = str(Path(__file__).resolve().parent.parent / "data" / "checkpoints.sqlite")

# 第 39 行改
checkpointer = SqliteSaver.from_conn_string(_CHECKPOINTS_PATH)
```

`data/` 目录已存在（SQLite DB 在那里），不引入新路径。`__init__.py` 已确保 `parent.parent` 是 `backend/`。

> **注**：`engine/memory/` 路径稳健化不归本 spec（坑三是预防性，那目录还空着）。未来在 `engine/memory/` 写代码时遵守相同规则即可。

---

## 7. 死代码清理

### 7.1 删 `backend/app/bridge/invoke.py`

- 49 行，0 调用点（grep 全 backend 仅 self-reference）
- Phase 1 commit (`8338c98`) 已用 `engine/graph.py` 取代
- 含 `init_globals` 特殊分发（test/calibrate/fingerprint/acceptance/memory 五个命令）——审计 2 提到"这部分未对着真实 novel_AI 跑过"，但既然 invoke.py 本身不再被任何代码调用，**未测试的代码 = 不存在的代码**，直接删

### 7.2 删 `backend/app/bridge/env_writer.py`

- 80 行，0 调用点
- 早期 spec 方案 §4.1 要求"先有正确 .env 再触发 run.py"，但 Phase 1 改成 `LLMRouter.install()` 直接覆盖 `api_client` 模块全局，**不再依赖 .env 文件**
- 删

### 7.3 计划文件回写

`docs/superpowers/plans/2026-06-26-novel-assistant-fusion.md` 的 Task 1-15 checkbox 全部从 `[ ]` 改 `[x]`，文件末尾追加一段：

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

---

## 8. Git divergence 处理

### 8.1 现状

```
origin/codex/phase1-engine-integration 独有: 34cffd8 fix: novel_AI/ 保持 gitignored 零修改，改用 monkey-patch 注入 SqliteSaver
codex/phase1-engine-integration (本地) 独有: 4e0d27d 完成novel-assistant × novel_AI 融合版
```

两边各 1 commit，方向一致（都提到 SqliteSaver + monkey-patch）。

### 8.2 步骤

1. `git fetch origin`（已做）
2. `git log -p origin/codex/phase1-engine-integration` 看 `34cffd8` 的实际 diff，判断是否与本地 `graph.py` 冲突
3. 优先 `git rebase origin/codex/phase1-engine-integration`：
   - 如果远端 commit 已被本地 `4e0d27d` 覆盖（如本地 `graph.py` 已含 monkey-patch），rebase 会判定"已应用"并跳过
   - 如果有真冲突，逐文件解
4. rebase 失败时改用 `git merge --no-ff origin/codex/phase1-engine-integration`
5. 同步完成后，本 spec 改动作为 1 个新 commit 推到分支

### 8.3 风险

- 远端 `34cffd8` 题目提到"monkey-patch 注入 SqliteSaver"——本地 `graph.py:43-61` 已含等效实现。rebase 大概率判定"已应用"。
- 万一远端有 novel_AI/ 改动 → 违反 CLAUDE.md "novel_AI/ 保持零修改"，需要保留远端版本并把本地对应改动丢掉。

---

## 9. 测试

新建 `backend/tests/test_phase1_5_smoke.py`（不强制 pytest，直接 `python -m tests.test_phase1_5_smoke` 跑通即可），包含 5 个 smoke：

### 9.1 冷启动 smoke

```python
# 用 FastAPI TestClient 启动一次
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)
assert client.get("/health").json() == {"status": "ok"}

# 验证 role_assignments 表恰好 15 行
from app.database import SessionLocal
from app.models import RoleAssignment
db = SessionLocal()
assert db.query(RoleAssignment).count() == 15
```

### 9.2 SSE 端到端

```python
# 触发 test 命令（Mock LLM，无需真 Key）
run = client.post(f"/projects/{project_id}/bridge/run", json={"command": "test", "args": []}).json()
assert run["status"] == "pending"

# 通过 httpx 拉 SSE
import httpx, json
events = []
with httpx.stream("GET", f"http://testserver/projects/{project_id}/bridge/stream?run_id={run['id']}") as r:
    for line in r.iter_lines():
        if line.startswith("data: "):
            events.append(json.loads(line[6:]))
        if events and events[-1].get("event") == "done":
            break

event_types = {e["event"] for e in events}
assert "log" in event_types
assert "done" in event_types
assert events[-1].get("exit_code") == 0
```

### 9.3 并发互斥

```python
# 启动第一个 run
r1 = client.post(f"/projects/{project_id}/bridge/run", json={"command": "test"}).json()
# 立刻再起一个，预期 409
r2 = client.post(f"/projects/{project_id}/bridge/run", json={"command": "test"})
assert r2.status_code == 409

# 验证 _project_locks 状态
from app.api.bridge import _get_project_lock
assert _get_project_lock(project_id).locked() is True
```

### 9.4 checkpoints.sqlite 路径

```python
# 跑一次 test 命令后，断言 backend/data/checkpoints.sqlite 存在
import os
assert os.path.exists("backend/data/checkpoints.sqlite"), "checkpoints.sqlite 应在 backend/data/，不在 cwd"

# 断言 cwd 下没有 stray 文件
assert not os.path.exists("checkpoints.sqlite"), "cwd 下不应有 stray checkpoints.sqlite"
```

### 9.5 前端构建

不在 Python 测试内，由 `cd frontend && npm run build` 单独验证。CI 友好做法是把它加到 `package.json::scripts.test`，但 spec 内只声明"必须通过"。

---

## 10. 风险与开放问题

### 10.1 已知风险

- **asyncio.to_thread 跨线程上下文**：LLM 调用是同步阻塞（`httpx.Client` / `Anthropic`），10 秒~60 秒/次。默认 ThreadPoolExecutor 池大小 `min(32, cpu+4)`，单实例支持 ~30 个并发 project run。超出后会排队。MVP 阶段单用户场景够用。
- **BridgeRun 表与 in-memory 锁一致性**：server 重启后 `_project_locks` 清空，但 DB 里 `BridgeRun.status='running'` 的孤儿行不会被解锁。需运维手工清理，或作为后续 Spec D 单独处理。
- **远端 commit `34cffd8` 内容未知**：rebase 前必须先看 diff。如果远端 commit 改了 `novel_AI/`，违反 CLAUDE.md，需要报警并保留远端版本。

### 10.2 不解决

- 异步 LLM 调用改造（坑一完全版）—— scope 过大，归未来
- `astream_events` 节点级事件（坑四完全版）—— 归 Spec C
- Pydantic schema 实际接进 StateGraph（引擎状态层升级）—— 归 Spec B
- 记忆系统路径（坑三）—— 预防性，等 `engine/memory/` 写新代码时再处理

### 10.3 验收门槛

- 5 项 smoke 全部通过
- `frontend npm run build` 无 TS 错
- `backend python -m tests.test_phase1_5_smoke` 全部断言通过
- 现有 `python run.py test` 仍能跑（`command="test"` 路径不变）
- git rebase / merge 无冲突，本分支与 origin 同步

---

## 11. 实现 plan 衔接

本 spec 完成后，调用 writing-plans skill 把每一节展开成可执行步骤。预期 plan 含 ~8-10 个 step：

1. `git fetch` + 检查远端 commit
2. 决定 rebase vs merge + 执行
3. 改 `engine/graph.py` 的 `checkpoints.sqlite` 路径
4. 改 `bridge.py`：加 `_project_locks` + `asyncio.to_thread` 包装
5. 删 `bridge/invoke.py` 和 `bridge/env_writer.py`
6. 改前端 `types.ts::BridgeLogLine.event` union
7. 改前端 `BridgeConsole.tsx`：4 个 listener + 清空硬编码路径
8. 新建 `backend/tests/test_phase1_5_smoke.py`
9. 跑 5 项 smoke 全部通过
10. 回写 `docs/superpowers/plans/...md` 的 checkbox + Phase 1.5 段
11. 提交 commit
