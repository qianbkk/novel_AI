# 融合系统执行手册：novel-assistant × novel_AI

执行者：具备 Python/FastAPI/SQLAlchemy/React/TypeScript 能力的工程 AI。
本文档是唯一依据，按章节顺序实施，每章末尾的"验收标准"作为完成判据。

---

## 0. 系统目标

把两个独立项目合并成一个：用户只接触 `novel-assistant` 的 Web 界面；`novel_AI`
（LangGraph 多 Agent 写作引擎）作为子进程被后端调起，源码不做任何修改。
模型用哪个供应商、哪个角色配哪个模型，由用户在设置页里配置，不写死在代码里。

两个仓库平级存放：

```
~/projects/novel-assistant/   # 本次构建的目标，下面所有路径相对于它
~/projects/novel_AI/          # 既有仓库，本手册中只读不改
```

---

## 1. 数据模型

在 `backend/app/models.py` 追加以下表（不修改任何既有表的字段）：

```python
class Provider(Base):
    __tablename__ = "providers"
    id = Column(String, primary_key=True, default=gen_id)
    name = Column(String, nullable=False)
    provider_type = Column(String, nullable=False)  # anthropic|deepseek|gemini|kimi|minimax|custom
    api_base = Column(String, nullable=True)         # custom 必填，其余留空用各自默认地址
    api_key = Column(String, nullable=False)
    default_model = Column(String, nullable=False)
    extra_json = Column(JSON, nullable=True)         # minimax 存 {"group_id": "..."}
    needs_proxy = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)


class RoleAssignment(Base):
    __tablename__ = "role_assignments"
    id = Column(String, primary_key=True, default=gen_id)
    role_key = Column(String, nullable=False, unique=True)  # 见 §3.1 注册表，15 条固定记录
    provider_id = Column(String, ForeignKey("providers.id"), nullable=True)
    model_override = Column(String, nullable=True)


class BridgeRun(Base):
    __tablename__ = "bridge_runs"
    id = Column(String, primary_key=True, default=gen_id)
    project_id = Column(String, ForeignKey("projects.id"))
    command = Column(String, nullable=False)        # planner|bootstrap|run|status|dashboard|...
    args_json = Column(JSON, nullable=True)
    status = Column(String, default="pending")       # pending|running|done|failed
    exit_code = Column(Integer, nullable=True)
    stdout_text = Column(Text, nullable=True)
    started_at = Column(DateTime, default=datetime.utcnow)
    finished_at = Column(DateTime, nullable=True)


class NovelAIBinding(Base):
    __tablename__ = "novel_ai_bindings"
    id = Column(String, primary_key=True, default=gen_id)
    project_id = Column(String, ForeignKey("projects.id"), unique=True)
    novel_ai_dir = Column(String, nullable=False)
    novel_id = Column(String, nullable=False)
```

在 `Project` 表追加两个字段：

```python
budget_limit_usd = Column(Float, nullable=True)
novel_ai_status = Column(String, default="not_started")
# not_started | concept_pushed | planner_done | bootstrap_done | writing | done
```

在 `WorldSetting` 表追加一个字段：

```python
novel_ai_raw_setting_json = Column(JSON, nullable=True)
```

**验收标准**：`alembic`（或直接 `Base.metadata.create_all`，本项目当前用的是后者）能在
空数据库上无报错建出全部新表；启动一次 FastAPI 服务，访问 `/docs` 能看到新表对应的
模型已加载（即使还没有路由）。

---

## 2. 模型路由角色注册表

新建 `backend/app/bridge/role_registry.py`：

```python
ROLE_REGISTRY = [
    {"role_key": "structured_logic",  "label": "结构化逻辑（世界观/大纲）", "namespace": "novel_assistant"},
    {"role_key": "creative_detail",   "label": "创意细节（人物/伏笔）",     "namespace": "novel_assistant"},
    {"role_key": "consistency_check", "label": "一致性复核",               "namespace": "novel_assistant"},
    {"role_key": "orchestrator",   "label": "Orchestrator 调度",     "namespace": "novel_ai"},
    {"role_key": "planner",        "label": "Planner 设定包生成",     "namespace": "novel_ai"},
    {"role_key": "outline",        "label": "Outline 任务单拆解",     "namespace": "novel_ai"},
    {"role_key": "writer",         "label": "Writer 正文生成",        "namespace": "novel_ai"},
    {"role_key": "normalizer",     "label": "Normalizer 去AI腔",      "namespace": "novel_ai"},
    {"role_key": "compliance",     "label": "Compliance 合规检查",     "namespace": "novel_ai"},
    {"role_key": "checker_main",   "label": "Checker 主评",           "namespace": "novel_ai"},
    {"role_key": "checker_cross1", "label": "Checker 交叉校验1",       "namespace": "novel_ai"},
    {"role_key": "checker_cross2", "label": "Checker 交叉校验2",       "namespace": "novel_ai"},
    {"role_key": "rewriter",       "label": "Rewriter 重写",          "namespace": "novel_ai"},
    {"role_key": "tracker",        "label": "Tracker 记忆更新",        "namespace": "novel_ai"},
    {"role_key": "summarizer",     "label": "Summarizer 长程摘要",     "namespace": "novel_ai"},
]
```

`namespace="novel_ai"` 的 12 条，`role_key` 必须和 `novel_AI/api_client.py` 里
`MODEL_ROUTES` 字典的 key **逐字相同**（这是约束，不是命名建议）。`namespace=
"novel_assistant"` 的 3 条供 `backend/app/llm_router.py` 使用，与 novel_AI 无关。

启动时（`main.py` 的 startup 事件里）执行一次：对 `ROLE_REGISTRY` 里每一条，若
`role_assignments` 表里没有对应 `role_key` 的记录，插入一条 `provider_id=NULL`
的空记录。这样前端拉取角色列表时永远是全量 15 条，不需要前端自己拼接"已配置 +
未配置"的差集。

**验收标准**：启动一次服务后查询 `role_assignments` 表，恰好 15 行，`role_key`
与上表完全一致。

---

## 3. Provider / RoleAssignment API

新建 `backend/app/api/providers.py`：

```
GET    /providers              列出全部
POST   /providers              新增，body: {name, provider_type, api_base, api_key, default_model, extra_json, needs_proxy}
PUT    /providers/{id}         修改，body 同上（全字段覆盖）
DELETE /providers/{id}         删除前检查：若有 role_assignments.provider_id 指向它，
                                把那些记录的 provider_id 置空，再删除（不报错拦截，静默处理）
```

新建 `backend/app/api/role_assignments.py`：

```
GET /role-assignments          返回 15 条，每条附带 provider 的 name/provider_type（前端展示用）
PUT /role-assignments/{role_key}   body: {provider_id, model_override}
```

**验收标准**：用 `TestClient` 跑通：新增一个 `provider_type=deepseek` 的 Provider，
把 `role_key=writer` 绑定给它，`GET /role-assignments` 返回里 `writer` 那一条的
`provider_id` 与刚创建的一致。

---

## 4. novel_AI 桥接执行器

### 4.1 写 `.env`

新建 `backend/app/bridge/env_writer.py`：

```python
from pathlib import Path
from ..models import Provider

PROVIDER_ENV_KEYS = {
    "anthropic": "ANTHROPIC_API_KEY",
    "deepseek": "DEEPSEEK_API_KEY",
    "gemini": "GEMINI_API_KEY",
    "kimi": "KIMI_API_KEY",
    "minimax": "MINIMAX_API_KEY",
    "custom": "CUSTOM_API_KEY",
}

def write_env(novel_ai_dir: str, providers_in_use: list[Provider], proxy_url: str | None):
    lines, written = [], set()
    for p in providers_in_use:
        if p.provider_type in written:
            continue
        written.add(p.provider_type)
        lines.append(f"{PROVIDER_ENV_KEYS[p.provider_type]}={p.api_key}")
        if p.provider_type == "minimax":
            lines.append(f"MINIMAX_GROUP_ID={(p.extra_json or {}).get('group_id', '')}")
        if p.provider_type == "custom":
            lines.append(f"CUSTOM_API_BASE={p.api_base or ''}")
            lines.append(f"CUSTOM_MODEL_ID={p.default_model}")
        if p.needs_proxy and proxy_url:
            lines.append(f"HTTPS_PROXY={proxy_url}")
            lines.append(f"HTTP_PROXY={proxy_url}")
    Path(novel_ai_dir, ".env").write_text("\n".join(lines), encoding="utf-8")
```

约束依据（novel_AI 源码已确认，不是推测）：`run.py` 启动时自己逐行解析 `.env`
并无条件写入 `os.environ`；`api_client.py` 的各 Provider 密钥是模块顶层常量，
`import` 那一刻读一次环境变量。所以必须保证：**先有正确的 `.env` 文件内容，
再触发 `run.py` 执行**，顺序不能反。

### 4.2 角色路由补丁 + 调用

新建 `backend/app/bridge/invoke.py`：

```python
import sys, json, asyncio, tempfile
from pathlib import Path

_BOOTSTRAP = '''
import sys, os, json, runpy

novel_ai_dir = sys.argv[1]
overrides_file = sys.argv[2]
forward_args = sys.argv[3:]

sys.path.insert(0, novel_ai_dir)

# 必须在 import api_client 之前，把 .env 内容加载进 os.environ。
# api_client.py 的各 API_KEY 是模块级常量，import 那一刻读一次环境变量；
# 如果指望 run.py 自己内部那段 .env 加载逻辑，那时 api_client 已经被
# 下面这行 import 过、常量早就被冻成空字符串了——必须抢在 import 之前做。
env_path = os.path.join(novel_ai_dir, ".env")
if os.path.exists(env_path):
    for line in open(env_path, encoding="utf-8"):
        line = line.strip()
        if line and "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            os.environ[k.strip()] = v.strip()

import api_client
with open(overrides_file, encoding="utf-8") as f:
    overrides = json.load(f)
api_client.MODEL_ROUTES.update({k: tuple(v) for k, v in overrides.items()})

sys.argv = ["run.py"] + forward_args
runpy.run_path(os.path.join(novel_ai_dir, "run.py"), run_name="__main__")
'''

async def invoke(novel_ai_dir: str, command: str, args: list[str],
                  role_overrides: dict[str, list[str]], on_line) -> int:
    """
    role_overrides: {"writer": ["anthropic", "claude-sonnet-4-5"], ...}
                    只包含 ROLE_REGISTRY 里 namespace=novel_ai 的角色。
    on_line: callable(str) -> None，每收到一行 stdout 调用一次（用于 SSE 转发）。
    返回子进程退出码。
    """
    tmp_dir = Path(tempfile.mkdtemp())
    bootstrap_path = tmp_dir / "bootstrap.py"
    bootstrap_path.write_text(_BOOTSTRAP, encoding="utf-8")
    overrides_path = tmp_dir / "overrides.json"
    overrides_path.write_text(json.dumps(role_overrides), encoding="utf-8")

    proc = await asyncio.create_subprocess_exec(
        sys.executable, str(bootstrap_path), novel_ai_dir, str(overrides_path),
        command, *args,
        cwd=novel_ai_dir,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
    )
    async for raw in proc.stdout:
        on_line(raw.decode("utf-8", errors="replace").rstrip())
    return await proc.wait()
```

> 已实测验证（用结构等价的替身脚本，模拟 api_client.py 的模块级常量 + 无 main()
> 的 run.py + 手动 .env 加载）：第一版实现把 `import api_client` 放在了"让
> run.py 自己加载 .env"之前，导致 API_KEY 常量被冻成空字符串——上面这版已经
> 修复（自己先读 `.env` 写进 `os.environ`，再 `import api_client`），断言全部通过。

不直接 `import orchestrator` 调用、而走子进程的理由：novel_AI 依赖
`langgraph`/`anthropic`/`jieba`，不并入 FastAPI 进程的依赖树，两个系统在依赖层
完全解耦，子进程启动开销（约1-2秒）相对于一次 LLM 调用（几十秒）可忽略。

**验收标准**：在没有任何真实 API Key 的情况下，调用
`invoke(novel_ai_dir, "test", [], {}, print)`，断言能跑起来并最终返回退出码
（`novel_AI` 自带 `python run.py test` 是 20 项集成测试，含 Mock LLM，不需要真实
Key 就能跑完）。这是验证桥接脚本本身没写错的第一道关卡，必须先过这一关，
再进行任何真实 Key 的测试。

### 4.3 触发入口

新建 `backend/app/api/bridge.py`，每个端点的实现都是："从 DB 取出该 project 绑定的
`novel_ai_dir`，调 `env_writer.write_env`，组装 `role_overrides`（查
`role_assignments` 表里 `namespace=novel_ai` 的全部记录），起一个 `BridgeRun` 记录，
`await invoke(...)`，把每行 stdout 同时写进一个 `asyncio.Queue`（复用
`worldbuild/orchestrator.py` 里 `get_job_queue` 的同款模式）供 SSE 端点消费，
结束后把 `exit_code`/`stdout_text` 写回 `BridgeRun`。"

```
POST /projects/{id}/bridge/run         body: {"command": "planner", "args": []}
                                         返回 {"bridge_run_id": "..."}
GET  /projects/{id}/bridge/stream      query: run_id=<bridge_run_id>，SSE 推送每行日志
                                         + 完成时推送 {"event": "done", "exit_code": N}
```

**并发约束**：同一个 `project_id` 同一时间只允许一个 `BridgeRun` 处于 `running`
状态（因为会争抢同一个 `novel_ai_dir/.env` 文件）。在 `POST .../bridge/run` 里
先查是否已有 `running` 的记录，若有直接返回 409。

**验收标准**：调用 `POST .../bridge/run {"command":"test","args":[]}`，SSE
流里能收到 `python run.py test` 的真实输出，最终 `exit_code=0`（20/20 通过）。

---

## 5. 设定包双向桥接

### 5.1 推送（novel-assistant → novel_AI）

不尝试逆向构造 `setting_package.json` 的精确字段（该文件由 `agents/planner_agent.py`
生成，其完整 schema 不在本手册的确认范围内）。改为只写入**已从源码 100% 确认**的
`config/novel_config.json`，把我们的世界构建结果压缩成一段结构化文本传给
`setting_concept` 字段，交给 novel_AI 自己的 Planner 去生成完整设定包。

新建 `backend/app/bridge/setting_sync.py`：

```python
async def push_setting_concept(project_id: str, novel_ai_dir: str, db):
    project = db.get(Project, project_id)
    world = db.query(WorldSetting).filter_by(project_id=project_id).first()
    characters = db.query(Character).filter_by(project_id=project_id).all()
    factions = db.query(Faction).filter_by(project_id=project_id).all()

    concept = "\n".join([
        f"世界观：{world.world_view}",
        f"故事核心：{world.story_core}",
        "主要人物：" + "；".join(f"{c.name}（{c.role}）" for c in characters),
        "主要势力：" + "；".join(f.name for f in factions),
    ])
    novel_config = {
        "novel_id": project.id,
        "platform": "fanqie",
        "genre": project.genre,
        "setting_concept": concept,
        "budget_limit_usd": project.budget_limit_usd or 500.0,
    }
    Path(novel_ai_dir, "config", "novel_config.json").write_text(
        json.dumps(novel_config, ensure_ascii=False, indent=2), encoding="utf-8")
    project.novel_ai_status = "concept_pushed"
    db.commit()
```

### 5.2 回灌（novel_AI → novel-assistant）

`python run.py planner` 跑完后调用：

```python
KNOWN_CHARACTER_KEYS = ["characters", "main_characters", "character_list"]
KNOWN_POWER_KEYS = ["power_system", "power_levels", "ability_system"]

async def pull_setting_package(project_id: str, novel_ai_dir: str, db):
    raw = json.loads(
        Path(novel_ai_dir, "output", "setting_package.json").read_text(encoding="utf-8")
    )
    world = db.query(WorldSetting).filter_by(project_id=project_id).first()
    world.novel_ai_raw_setting_json = raw   # 唯一真相来源，任何前端用不到的字段都还在这里

    project = db.get(Project, project_id)
    if raw.get("title_candidates") and not project.title:
        project.title = raw["title_candidates"][0]

    world.plot_skeleton_json = [
        {"title": a.get("arc_name"), "summary": a.get("arc_goal")}
        for a in raw.get("arc_outline", [])
    ]

    for key in KNOWN_CHARACTER_KEYS:
        if key in raw:
            for item in raw[key]:
                db.add(Character(project_id=project_id, name=item.get("name"),
                                  role=item.get("role"), detail_json=item))
            break

    for key in KNOWN_POWER_KEYS:
        if key in raw:
            ps = raw[key]
            db.add(PowerSystem(project_id=project_id, name=ps.get("name", "力量体系"),
                                description=ps.get("description"), tiers_json=ps.get("tiers")))
            break

    project.novel_ai_status = "planner_done"
    db.commit()
```

`title_candidates` 和 `arc_outline` 已从 `run.py` 源码逐字确认，结构化映射可靠；
其余字段用候选 key 列表做best-effort 解析，解析不到也不报错——`
novel_ai_raw_setting_json` 永远完整保留原文件，前端需要的话可以直接展示这个
JSON 原文，不依赖结构化解析是否命中。

### 5.3 API

```
POST /projects/{id}/bridge/push-concept     调 push_setting_concept
POST /projects/{id}/bridge/pull-setting      调 pull_setting_package（在 planner
                                              命令的 BridgeRun 完成后，前端自动接着调这个）
```

**验收标准**：构造一份固定测试数据（1个 WorldSetting + 2个 Character），调用
`push_setting_concept`，断言 `novel_ai_dir/config/novel_config.json` 存在且
`setting_concept` 字段包含两个人物名字。再手工放一份样例
`output/setting_package.json`（含 `title_candidates`/`arc_outline`/`characters`
三个字段）到测试目录，调用 `pull_setting_package`，断言
`WorldSetting.novel_ai_raw_setting_json` 等于原文件内容，且至少新增了对应数量的
`Character` 行。

---

## 6. 章节导入桥接

新建 `backend/app/bridge/chapter_import.py`：

```python
async def import_chapters_from_novel_ai(project_id: str, novel_ai_dir: str, db) -> list[dict]:
    imported = []
    chapters_dir = Path(novel_ai_dir, "output", "chapters")
    for txt_path in sorted(chapters_dir.glob("ch_*.txt")):
        n = int(txt_path.stem.split("_")[1])
        if db.query(Chapter).filter_by(project_id=project_id, chapter_no=n).first():
            continue
        content = txt_path.read_text(encoding="utf-8")
        meta_path = txt_path.with_name(txt_path.stem + "_meta.json")
        meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
        result = await add_chapter(project_id, n, meta.get("title"), content, db)
        result["novel_ai_score"] = meta.get("score")
        result["novel_ai_rewrite_count"] = meta.get("rewrite_count")
        imported.append(result)
    return imported
```

`add_chapter` 是已有函数（`backend/app/rag/retrieval.py`），导入时会自动触发既有的
embed + 人物标记 + 重复度检测——这是独立于 novel_AI 自身 Checker 的第二道检测，
两者并存，不是替代关系。`score`/`rewrite_count` 两个字段已从 `run.py` 的
`cmd_show()` 源码确认存在；如果实际生成出的 `meta.json` 里还有别的字段（比如字数），
用 `meta.get(key)` 按需追加即可，不影响已确认字段的可靠性。

API：

```
POST /projects/{id}/bridge/import-chapters
```

**验收标准**：在测试目录手工放两个 `ch_0001.txt`/`ch_0001_meta.json`（含 `score`/
`rewrite_count`），调用后断言 `Chapter` 表新增一行，且 `EmbeddingChunk` 表对应
新增一条记录（证明 embed 流程被正确触发）。

---

## 7. 控制面 API

新建 `backend/app/bridge/reports.py`：

```python
def read_status(novel_ai_dir: str) -> dict:
    return json.loads(
        Path(novel_ai_dir, "output", "orchestrator_state.json").read_text(encoding="utf-8")
    )

def read_pending(novel_ai_dir: str) -> list[dict]:
    return read_status(novel_ai_dir).get("human_pending", [])

def read_budget_log(novel_ai_dir: str) -> list[dict]:
    path = Path(novel_ai_dir, "logs", "budget_log.jsonl")
    if not path.exists():
        return []
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
```

`status`/`pending` 直接解析结构化 JSON 文件，可靠。`dashboard`/`fingerprint`/`scan`
三个命令目前只把结果打印到 stdout、没有单独的 JSON 导出函数——v1 方案：通过
`invoke()` 跑这三个命令，把完整 stdout 文本存进 `BridgeRun.stdout_text`，前端用
`<pre>` 原样展示。不在本手册范围内追加修改 novel_AI 源码。

API：

```
GET  /projects/{id}/bridge/status       调 read_status
GET  /projects/{id}/bridge/pending      调 read_pending
GET  /projects/{id}/bridge/budget       调 read_budget_log
POST /projects/{id}/bridge/run {"command": "dashboard"}  /  "fingerprint"  /  "scan"
     —— 复用 §4.3 的通用触发入口，结果走 stdout_text
POST /projects/{id}/bridge/review       人工审核动作：body {"task_id", "action": "accept"|"reject"|"edit", "edited_content"}
                                          实现：读 output/orchestrator_state.json，
                                          在 human_pending 列表里移除对应 task_id，
                                          若 action="edit" 则同时把 edited_content
                                          写回对应章节文件，写回 orchestrator_state.json
```

**验收标准**：手工放一份样例 `orchestrator_state.json`（含 2 条 `human_pending`），
`GET .../bridge/pending` 返回这 2 条；调用 `POST .../bridge/review` 处理掉 1 条后，
重新 `GET` 只剩 1 条。

---

## 8. 前端

### 8.1 新增页面与路由（`frontend/src/App.tsx` 追加）

| 路由 | 文件 | 内容 |
|---|---|---|
| `/settings/providers` | `pages/Providers.tsx` | Provider 列表 + 新增/编辑表单（name/provider_type下拉/api_base/api_key/default_model） |
| `/settings/roles` | `pages/RoleAssignments.tsx` | 15行表格，每行：角色label + Provider下拉框 + 模型覆盖输入框 |
| `/projects/:id/bridge` | `pages/BridgeConsole.tsx` | 按钮组（推送设定/生成设定包/黄金三章/写N章/状态/看板/预算/待审核） + 实时日志(SSE) |

### 8.2 `api/client.ts` 追加方法

```typescript
listProviders, createProvider, updateProvider, deleteProvider,
listRoleAssignments, updateRoleAssignment,
triggerBridge(projectId, command, args),       // POST .../bridge/run
bridgeStreamUrl(projectId, bridgeRunId),         // GET .../bridge/stream
pushConcept(projectId), pullSetting(projectId),
importChapters(projectId),
getBridgeStatus(projectId), getBridgePending(projectId), getBridgeBudget(projectId),
submitReview(projectId, taskId, action, editedContent?)
```

### 8.3 `types.ts` 追加类型

```typescript
export interface Provider {
  id: string; name: string;
  provider_type: "anthropic"|"deepseek"|"gemini"|"kimi"|"minimax"|"custom";
  api_base: string | null; default_model: string; needs_proxy: boolean;
}
export interface RoleAssignment {
  role_key: string; label: string; namespace: "novel_assistant"|"novel_ai";
  provider_id: string | null; provider_name: string | null; model_override: string | null;
}
export interface BridgeLogEvent {
  event: "line" | "done";
  text?: string;
  exit_code?: number;
}
```

### 8.4 `BridgeConsole.tsx` 实现要点

- 复用 `WorldBuild.tsx` 里 `EventSource` 的写法：点按钮 → `POST .../bridge/run`
  拿到 `bridge_run_id` → `new EventSource(bridgeStreamUrl(...))` → `addEventListener
  ("line", ...)` 把文本追加进一个数组 state，渲染成滚动日志区（`<pre>` 标签，
  `overflow-y: auto`，新行追加后自动滚到底部）。
- 顶部一行按钮：`推送设定` `生成设定包` `黄金三章` `写10章`（输入框可调章数）
  `查看状态` `质量看板` `预算报告` `一致性扫描` `文风指纹`，每个按钮对应一条
  固定的 `command` 参数。
- 收到 `done` 事件后，若刚才跑的是 `run` 命令，自动接着调一次
  `importChapters(projectId)`；若是 `planner`，自动接着调一次
  `pullSetting(projectId)`——这两步在前端做自动串联，不需要用户多点一次。

**验收标准**：`npm run build` 无类型错误；手动在浏览器里点"查看状态"按钮，
能看到从后端转发过来的 `orchestrator_state.json` 内容（即使 novel_AI 还没真正
跑过、文件不存在，也应该展示一个清晰的"尚未初始化"提示而不是白屏报错）。

---

## 9. 完整 API 总览

| 方法 | 路径 | 来源 |
|---|---|---|
| 既有不变 | `/projects*`, `/projects/{id}/worldbuild/*`, `/projects/{id}/chapters*` | 不动 |
| `GET/POST/PUT/DELETE` | `/providers`, `/providers/{id}` | §3 |
| `GET/PUT` | `/role-assignments`, `/role-assignments/{role_key}` | §3 |
| `POST` | `/projects/{id}/bridge/run` | §4.3 |
| `GET` | `/projects/{id}/bridge/stream` | §4.3 |
| `POST` | `/projects/{id}/bridge/push-concept` | §5.3 |
| `POST` | `/projects/{id}/bridge/pull-setting` | §5.3 |
| `POST` | `/projects/{id}/bridge/import-chapters` | §6 |
| `GET` | `/projects/{id}/bridge/status` `/pending` `/budget` | §7 |
| `POST` | `/projects/{id}/bridge/review` | §7 |

---

## 10. 端到端使用序列

```
1. 用户在 /settings/providers 添加若干 Provider 账号
2. 用户在 /settings/roles 给 15 个角色分别选 Provider（留空则该角色不可用，
   触发对应命令时后端应返回明确错误："角色 writer 未配置 Provider"）
3. 新建项目 → 世界构建10阶段（既有功能，不变）
4. 项目设置里填 novel_ai_dir 和预算上限（写入 NovelAIBinding / Project.budget_limit_usd）
5. /projects/:id/bridge 页：点"推送设定" → "生成设定包"（自动回灌）→
   人工查看 WorldSetting.novel_ai_raw_setting_json 展示的设定包内容并确认
6. 点"黄金三章" → 查看 output/bootstrap_candidates.json（新增一个只读接口直接转发
   这个文件内容，前端展示三版打分对比）→ 用户在网页里选定某一版（调用
   `POST .../bridge/run {"command":"style","args":["select","<N>","<X>"]}`，
   对应 novel_AI 的 `python tools/bootstrap.py select N X`）
7. 点"写10章" → SSE日志实时展示 → 完成后自动 import-chapters
8. /projects/:id/chapters 页查看导入的章节，叠加查看 novel_ai_score；
   /projects/:id/bridge 页查看质量看板/预算报告
9. 遇到 human_pending 任务时，/projects/:id/bridge 页展示待审核列表，
   用户点击处理（接受/重写/编辑），提交 review
```

---

## 11. 实施顺序与每阶段验收

严格按此顺序实施，每阶段验收标准全部通过才能进入下一阶段：

1. **§1 数据模型** → 验收：建表无误
2. **§2 角色注册表** → 验收：15条种子数据
3. **§3 Provider/RoleAssignment API** → 验收：CRUD 测试通过
4. **§4 桥接执行器** → 验收：`python run.py test` 能通过子进程跑通并拿到退出码0
5. **§5 设定包双向桥接** → 验收：固定测试数据的推送/回灌测试通过
6. **§6 章节导入** → 验收：样例文件导入后 Chapter + EmbeddingChunk 行数正确
7. **§7 控制面 API** → 验收：样例 state 文件的 pending/review 流程测试通过
8. **§8 前端** → 验收：`npm run build` 通过，浏览器手动走一遍 §10 的序列 1-4 步
9. 真实 API Key 接入，先用 1-3 章小批量验证 novel_AI 实际产出质量，再决定是否
   批量生产——这一步没有自动化验收标准，结果好坏由用户主观判断。
