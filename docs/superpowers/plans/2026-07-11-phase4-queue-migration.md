# Phase 4 · BridgeRun 任务队列迁移路径

> 当下不需要实现，仅记录"将来真要多用户 + 高并发时"如何从 in-process subprocess
> 模式迁到真正的任务队列（Celery / RQ / Dramatiq / Arq 等）。

## 当前状态（2026-07-11）

`backend/app/api/bridge.py::_spawn_engine_subprocess` 走下面这条路径：

```
HTTP POST /projects/{pid}/bridge/run
   ↓
fastapi BackgroundTasks.add_task
   ↓
_spawn_engine_subprocess
   ↓
subprocess.Popen([worker, run_id, project_id, cmd, args, outline_mode],
                 stdout=PIPE, stderr=STDOUT, env=…, cwd=…)
   ↓
threading.Thread drain stdout → put to SSE Queue + DB.BridgeRun.stdout_text
```

特性：
- 单进程：uvicorn worker 进程内 fork 出 engine 子进程
- 内存 in-memory SSE Queue：SSE consumer 读 → done 事件触发清理（避免泄漏）
- DB 层 BridgeRun 表记录每次 run：status / exit_code / stdout_text / start/finish 时间
- 并发保护：DB 层 BridgeRun.status IN ('pending','running') 检查 + 启动时恢复 orphan

## 为什么"现在够用"

| 维度 | 单租户本地原型 | 为什么够 |
|---|---|---|
| 并发 | 你一个人写一部作品 | BridgeRun 同 project 二选一足够 |
| 失败可见 | stdout → SSE + DB.stdout_text | 看 status/pending 端点就行 |
| 状态持久化 | BridgeRun 表 + checkpoints.sqlite | 重启可恢复 |
| 跨设备 | 不支持（这是个人原型正常） | — |

并发证据：当前 BridgeRun 对同一 project 锁死——多次 run 之间有互斥。多 project 并发
理论上没限制（每个 project_id 独立 row），但单 uvicorn worker 同进程内 LLM 调用的
并发上限其实是 LLM provider（每个 project 一次 run，单 project 写同一份
checkpoints.sqlite）。本地单机一个 project_id 跑两章 = 等价于串行。

## 触发"需要任务队列"的信号

满足**任何一条**时启动 Phase 5（队列迁移）：

1. **多个用户同时 run**
   - 真有多租户，每个 user 跑各自项目时，希望别人跑不阻塞自己

2. **跨 worker 进程 / 多机**
   - 单 uvicorn worker 处理不了并发 LLM 调用 → 加 gunicorn 多 worker
   - 当前状态：双 worker 同进程锁状态（BridgeRun 互斥）能工作，但 SSE Queue 是
     worker-local 的——sse 客户端连 worker A，run 在 worker B，stream 就接不到了

3. **run 长度突破 30 分钟**
   - 当前 run_max_timeout 不限；但 SSE 连接、http2 配置都可能先崩
   - 队列方案天然擅长"任务超过 client 连接长度"

4. **失败重试**
   - 当前失败了只能人工跑 `reimport-chapters`
   - 队列方案自动 retry 简单

5. **真需要"调度"（cron / 周期 / pipeline）**
   - 比如"每天 0 点跑一章自动续写"
   - 队列原生支持 beat schedule

## 任务队列选项对照

| 选项 | 复杂度 | 依赖 | 适合 |
|---|---|---|---|
| **Celery** | 中 | Redis / RabbitMQ | 多 worker 跨机，工业级 |
| **RQ (Redis Queue)** | 低 | Redis | 简单的"扔后台跑" |
| **Dramatiq** | 中 | Redis / RabbitMQ | 类型化 actors，比 celery 现代化 |
| **Arq** | 低-中 | Redis | asyncio 友好 |
| **Huey** | 低 | SQLite / Redis | 跟我们的 SQLite DB 思路一致（轻量） |
| **自建 + APScheduler** | 中-高 | DB lock | 完全在主 DB 内做，但容易写错 |

**推荐**：如果真需要，最先评估 **Huey + SQLite**——它跟现在的 SQLite 单文件思路一致，
不需要新组件（Redis / RabbitMQ）。其次 **RQ + Redis**——简单、工业级、生态成熟。

## 迁移步骤（要做的清单）

不是今天做——但是写下来，将来满足 trigger 后照着走：

### Step 1：抽离"任务"形态

把现在的 `_spawn_engine_subprocess` 改成可被任何 backend 调用的 "task" 函数：

```python
# tasks.py
def run_bridge_task(run_id: str, project_id: str, command: str,
                   args: list, outline_mode: str):
    """run_id 负责：写 BridgeRun 进度、写 stdout、SSE 推送"""
    # 主体就是当前 _spawn_engine_subprocess 的逻辑
```

### Step 2：定 broker

最简单：Redis（成熟、便宜、digital ocean / upstash 等都能起）。
或：Huey 模式直接用 SQLite（同进程之外另开 worker 进程）。

### Step 3：替换 `_spawn_engine_subprocess` 为 `task.delay(...)`

```python
# 现在：
background_tasks.add_task(_spawn_engine_subprocess, run_id, project_id, ...)
# 改后：
from tasks import run_bridge_task
run_bridge_task.delay(run_id, project_id, command, args, outline_mode)
db.commit()  # BridgeRun 行已经 insert 在 commit 前
```

### Step 4：SSE 流式广播改成"订阅"

现在 SSE consumer 直接 in-process Queue.get。
队列方案：SSE 端点不再直接读 stdout，而是订阅"run progress" topic
（Redis Pub/Sub / broker 的 result backend），把每个进度事件 forward 到 SSE。

### Step 5：worker 部署

启动一个独立 `worker` 进程或容器：
```bash
celery -A tasks worker --loglevel=info
# 或
huey_consumer tasks.huey
```

桥接 deployment：docker-compose 加 worker service。k8s 加 Deployment。

### Step 6：保留"同步"路径作为 fallback

为开发 / 单测方便，让 `_spawn_engine_subprocess` 在 NOVEL_QUEUE_DISABLED=1 时
还是走 in-process 路径。这样 dev 模式不动，prod 走队列。

## 跟现有系统的兼容

迁移不需要"重写 day 0"：

- **BridgeRun 表结构不变**——队列存的是 run_id，DB 仍然存"这一次 run 的元数据"
- **SSE API 形状不变**——前端继续 GET /projects/{pid}/bridge/stream
- **错误恢复路径不变**——lifespan 启动仍跑 `_recover_orphan_bridge_runs`
  把 status='running' 但 finished_at IS NULL 的行标 failed，新 worker 不会再接

## 跟 Phase 4 多用户的关系

Phase 4 多用户认证只是引入 owner_id 隔离。如果走"严格多租户"模式，
队列方案还要加 per-user concurrency limit（不是单 project，而是 per-user

队列同时并发不能超过 N）。这层逻辑天然落在 worker 的 prefetch / rate limit 上，
比在 uvicorn 同步代码里写要干净。

## 当下不动的原因（重述）

YAGNI。当前是单租户本地原型，证据不足以支撑现在设计队列。等待 trigger 出现
再启动 Phase 5。内存 subprocess + SSE Queue + BridgeRun 表这套方案在当前规模
下足够好，不能预设"将来更复杂"。
