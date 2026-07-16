# Spec C — 节点级事件 UX 增强 — 设计文档

> **Date:** 2026-06-28
> **Branch:** `codex/phase1-engine-integration`
> **Status:** Design (待批准)
> **Scope:** Spec C（astream_events 节点级事件）— ponytail 增量版

---

## 1. 目标

当前 SSE 事件流只来自 `redirect_stdout` + `SSECapture` 抓 `agents/orchestrator.py` 主动 `print()` 的内容。前端看不到 LangGraph 节点级状态（"Writer 节点开始"、"Checker 节点结束"），用户只能等章节完成后从 `done` 事件看结果。

本 spec 加一层**节点级事件**到现有 SSE 流：
- 节点进入 → 推 `node_start` 事件
- 节点退出 → 推 `node_end` 事件
- **保留所有现有 print 抓取**（agent 的 `print("📋 拆解弧...")` 等调试输出仍有价值）
- **不**用 LangGraph `astream_events` API（避免 async 改造 + 双重 state 推进 + LangGraph 1.0+ API 变化）

**非目标**：
- 不替换 `print` 捕获为节点事件（不打破现有日志流）
- 不做节点进度条 / 质量评分可视化 / 状态机视图
- 不改 `run_graph_task` 签名 / `asyncio.to_thread` 模式 / `BackgroundTasks` 模式
- 不动 `novel_AI/` 任何文件

---

## 2. 改动面

| # | 文件 | 改动 |
|---|---|---|
| 1 | `backend/engine/graph.py` | `_build_with_checkpoint` 内用 `_NodeWrapper` 包装 `node_*`；`build_project_graph` 接受 `queue` 参数；`run_graph_task` 传 queue 进去 |
| 2 | `frontend/src/pages/BridgeConsole.tsx` | 加 2 个 `addEventListener`（`node_start` / `node_end`）；UI 顶部加"当前节点"状态条 |
| 3 | `frontend/src/types.ts` | `BridgeLogLine.event` union 加 `node_start` / `node_end` |
| 4 | `backend/tests/test_phase1_5_smoke.py` | 加 smoke 9：触发 `status` 命令，断言 SSE 流里有 `node_start` 或 `node_end` 事件 |

`backend/engine/graph.py` 内部加 `_NodeWrapper` 类（10 行内），不改其他文件。

---

## 3. 设计

### 3.1 `_NodeWrapper` 实现

```python
class _NodeWrapper:
    """Wrap a LangGraph node function to emit node_start/node_end events to the queue.
    ponytail: synchronous, safe to use inside asyncio.to_thread (queue.Queue is thread-safe)."""
    def __init__(self, name: str, fn, queue: Queue):
        self.name = name
        self.fn = fn
        self.queue = queue

    def __call__(self, state):
        self.queue.put({"event": "node_start", "node": self.name})
        try:
            return self.fn(state)
        finally:
            self.queue.put({"event": "node_end", "node": self.name})
```

### 3.2 `build_project_graph` 签名变化

```python
def build_project_graph(project_id: str, queue: Queue | None = None) -> Any:
```

向后兼容：`queue=None` 时不包装（用原 `getattr(_orch, f"node_{name}")` 直接注册）；传 queue 时用 `_NodeWrapper` 包。

### 3.3 `_build_with_checkpoint` 内部

```python
def _build_with_checkpoint():
    g = StateGraph(_orch.OrchestratorState)
    for name in ("load_arc_tasks", "get_next_task", "write_pipeline",
                 "rewrite", "save_and_track", "human_escalation"):
        fn = getattr(_orch, f"node_{name}")
        if queue is not None:
            fn = _NodeWrapper(name, fn, queue)
        g.add_node(name, fn)
    # ... edges 不变 ...
    return g.compile(checkpointer=checkpointer)
```

`run_graph_task` 调用 `build_project_graph(project_id, queue)` 传 queue 进去。

### 3.4 同步安全性

- `run_graph_task` 在 `asyncio.to_thread` 调度的 worker thread 里跑
- worker thread 里 LangGraph 节点同步执行（`node_*` 都是 `def`）
- `_NodeWrapper.__call__` 同步调 `self.queue.put(...)`
- `queue.Queue` 是线程安全的（CPython GIL 保证原子 `put`）
- SSE consumer `await asyncio.to_thread(queue.get)` 读到，emit SSE event
- 现有 `print` 抓取继续走 `SSECapture`，互不干扰

### 3.5 前端 UI

BridgeConsole 顶部加一个 status 区域：

```tsx
<div className="card mt-24">
  <span className="text-muted">当前节点：</span>
  {activeNode ? <strong>{activeNode}</strong> : <em>—</em>}
</div>
```

`activeNode` state 在 `node_start` 时设置，`node_end` 时清空。

`node_start` / `node_end` 事件结构：
```json
{"event": "node_start", "node": "write_pipeline"}
{"event": "node_end", "node": "write_pipeline"}
```

---

## 4. 风险

- **node_start / node_end 顺序**：同一节点可能多次进出（如 `rewrite` 在循环里）。每次都 emit 事件，前端按"最后一次 node_end"逻辑清空 `activeNode`。简单够用。
- **node 名称长**：`human_escalation` 等名字在 status 条上可能显示拥挤。前端可截断到 12 字符。
- **如果 node_* 抛异常**：`finally` 块保证 `node_end` 一定 emit。`except` 块在 `_run_bridge_async` 里接住，整体标记 `status=failed`，不影响节点事件流通。
- **novel_AI 节点改名**：节点名硬编码在 `graph.py:46-47`，是 spec 范围内的。`novel_AI/orchestrator.py` 改动不在本 spec 范围。

---

## 5. 测试

### 5.1 smoke 9（自动）

`backend/tests/test_phase1_5_smoke.py` 加 `smoke_9_node_events`：

```python
def smoke_9_node_events() -> None:
    """9/9: status 命令应该 emit 至少一个 node_start/node_end"""
    events = _run_bridge_command_shared(shared_client, f"smoke-node-{uuid.uuid4().hex[:8]}", "status")
    types = {e.get("event") for e in events}
    # status 命令不调 run_orchestrator（直接读 state 文件），所以可能没 node 事件
    # 改用 init_arc 或更重的命令，或直接验证 build_project_graph 单独跑出 node 事件
```

实际上 `status` 命令不调 graph（直接读 state），可能 0 个 node 事件。需要换命令——比如 `init_arc`（调用 `run_outline`，会触达 graph）。但 `init_arc` 也只调 `outline_agent.run_outline`，不走 LangGraph graph。

**真正能触发 node 事件的命令是 `run` / `resume`**，它们调 `run_orchestrator(state)`，里面会调 `graph.invoke()`。但 `run` 会真写章节，LLM 失败就报错。

替代方案：直接调 `build_project_graph` + 用 mock state 跑一次 invoke 验证 node 事件。

```python
def smoke_9_node_events() -> None:
    """9/9: graph 节点事件流通畅"""
    from engine.graph import build_project_graph, _NodeWrapper
    q = Queue()
    g = build_project_graph("smoke-node-1", q)
    # 调 g.invoke(initial_state) — 但需要 mock LLM
    # 简化：直接验证 _NodeWrapper 的基本行为
    called = []
    class _MockNode:
        def __call__(self, state): called.append("enter"); return state
    wrapped = _NodeWrapper("mock", _MockNode(), q)
    wrapped({})
    assert not q.empty()
    e1 = q.get(); assert e1["event"] == "node_start" and e1["node"] == "mock"
    e2 = q.get(); assert e2["event"] == "node_end" and e2["node"] == "mock"
    assert called == ["enter"]
```

这是 `_NodeWrapper` 的单元测试，**不**端到端跑 graph。简单可靠。

### 5.2 手动验证

- `npm run build` 通过
- 启动后端 + 前端，触发"写 N 章"按钮，浏览器日志区应看到 `[node_start] write_pipeline` 之类的输出

---

## 6. 实现 plan 衔接

调用 writing-plans skill（这次没有，先脑暴+spec+plan 同心圆走完后直接动手）：
- Task 1: 改 `graph.py` 加 `_NodeWrapper` + queue 参数
- Task 2: 改 `types.ts` + `BridgeConsole.tsx` 加 listener + 状态条
- Task 3: 加 smoke 9
- Task 4: 跑全套 smoke
- Task 5: commit + push

预期 ~5 个 step，1-2 小时可完成。
