# 架构现状速览

> 这是一份「现在长什么样 + 为什么」的速览入口，与 [CHANGELOG.md](../CHANGELOG.md) 时序累积分开。
> 任何改动涉及核心路径时，先查这里；改完有结构性影响时，回来更新这里。

---

## 1. 一句话

**FastAPI + React 嵌一个 LangGraph 9-Agent 长篇网文写作引擎，前置 10 阶段结构化世界构建，后置图谱+向量混合检索守一致性。单租户本地原型。**

---

## 2. 三层结构

```
┌────────────────────────── 浏览器 (React, Vite) ─────────────────────────┐
│ Pages: Dashboard · NewProject · Providers · RoleAssignments ·          │
│   WorldBuild · BridgeConsole · Chapters · RuleCenter · CharacterCard │
│ api/client.ts: fetch 封装（含 JSON 解析失败脱敏）                         │
│ types.ts ↔ backend Pydantic schema 严格 1:1                              │
└────────────────────────────────────────────────────────────────────────────┘
   ↑ VITE_API_BASE (default http://localhost:8132)
   │ SSE (EventSource) + fetch
   ↓
┌───────────────────── FastAPI 后端 (uvicorn :8132) ─────────────────────────┐
│                                                                            │
│  lifespan (main.py):                                                      │
│   - NOVEL_PRODUCTION=1 时强制 MASTER_KEY 已设 (fail-fast)               │
│   - run_migrations (idempotent ALTER TABLE ADD COLUMN)                  │
│   - seed_role_assignments (15 个写作角色种子)                            │
│   - _recover_orphan_bridge_runs (上一轮崩溃的 running 行标 failed)        │
│   - take_all_snapshots (sqlite online backup, 保留 10 份)               │
│                                                                            │
│  middleware:                                                               │
│   - CORSMiddleware (env ALLOWED_ORIGINS, 默认 localhost:5293)            │
│   - RateLimitMiddleware (env RATE_LIMIT_PER_MINUTE 默认 60;               │
│                          127.0.0.1 / ::1 默认豁免 — 个人使用是摩擦)       │
│                                                                            │
│  app/api/*: REST 路由                                                      │
│  app/worldbuild/*: 10 阶段 linear pipeline + SSE                          │
│  app/bridge/*:   与 engine 桥接 (push-concept → planner → pull → ...)    │
│  app/rag/*:      图谱 + 向量混合检索 (重复度 + 语义)                      │
│  app/security.py: Fernet + MASTER_KEY (env > 磁盘 > 临时生成)            │
│                                                                            │
│  引擎以 subprocess 模式跑（engine/workers/run_bridge_subprocess.py）。     │
│  uvicorn 重启不影响 in-flight run。                                       │
└────────────────────────────────────────────────────────────────────────────┘
   │ subprocess.Popen + stdout pipe + env injection
   ↓
┌────────────── LangGraph 引擎（独立 Python 进程）────────────────────────────┐
│                                                                            │
│  engine/graph.py: 状态机装配 + SSE 队列封装                                │
│  engine/orchestrator.py: 7 节点 LangGraph 状态机                          │
│  engine/agents/*: 9 个真实实现的 agent                                     │
│    planner / outline (batch|card|talk) / writer / normalizer /            │
│    compliance / checker (主评+2 路交叉) / rewriter (P0/P1/P2) /            │
│    tracker (L2 热冷分层) / summarizer (L5 弧档案) / init_arc              │
│  engine/llm/router.py: 6 provider + mock, length budget 控字数             │
│  engine/memory/manager.py: L2 热冷分层 + 风格样本切换 + 约束过期          │
│  engine/config/*: paths + prompt_templates + power_levels                  │
│  engine/tools/*: bootstrap / budget / scan / fingerprint / exporter...   │
│  engine/utils.py: atomic_write_json + parse_llm_json_response (3 策略)    │
│                                                                            │
│  audit_mode: 'full' (默认全链路) | 'draft' (writer + normalizer + tracker │
│              only; 个人试错用)                                            │
│  platform:    'fanqie' (默认跑番茄合规) | 'personal' / 'none' / 'internal' │
│              (跳过平台合规; checker 仍跑)                                  │
└────────────────────────────────────────────────────────────────────────────┘
   │
   ├─ SQLite:  backend/data/novel_assistant.db (业务)                      │
   │            + backend/data/checkpoints.sqlite (LangGraph 状态)        │
   ├─ JSON:    orchestrator_state.json                                     │
   │            setting_package.json                                       │
   │            chapters/ch_NNNN.txt + ch_NNNN_meta.json                   │
   │            memory/{l2,l5}/<novel_id>*.json                            │
   │            style_samples/*.txt (外部 + int_auto_* 自动提取)            │
   └─ 日志:     backend/logs/novel_ai.log (RotatingFileHandler 5MB×5)      │
```

---

## 3. 数据契约（"加字段五步流程")

`docs/root_cause_analysis.md` 第 4 节定下的 5 步——任何 schema 字段都要走完：

1. 改 `backend/schema/<schema>.schema.json`
2. 改生成端 prompt（planner.py / stages.py / rewriter.py 等）
3. 改消费端解析（setting_sync.py / chapter_import.py）
4. `python -m scripts.audit_project --strict`（暴露漂移）
5. `python -m pytest tests/test_invariants.py -v`（118 个 invariant 锁死）

**自由字段**（个人偏好、不影响引擎逻辑）可以放进 `Project.config_json` 的 freeform 部分，跳过 1-5 步。

---

## 4. 当前真实风险敞口

按 [Prioritize real risks over future armor](../.claude/projects/D--AI-Codex-workspace-Novel-AI/memory/prioritize-real-risks-over-future-armor.md) 排列。

| 风险 | 状态 | 缓解 |
|------|------|------|
| 本地磁盘故障 / 误删 | **真实最大风险** | lifespan sqlite online backup (10 份) 留 24h 内恢复能力。**仍缺**: 定时异地备份（建议配坚果云 WebDAV） |
| 真实生成质量没人验证 | **真实风险** | 真实模型（DeepSeek/Kimi/MiniMax/Anthropic）跑 worldview + 角色卡 + 章节后，质量靠肉眼观察，无自动评估 |
| 跨库一致性窗口 | 受控但需知情 | `novel_assistant.db` 与 `checkpoints.sqlite` 无跨文件事务；靠 `chapter_import.py` 按 `chapter_no` 幂等去重 |

## 已冻结的事

参见 README "部署" 段落与 memory：认证、多 worker、CORS 收紧、限流强化、分布式队列等"生产级护栏"全部冻结到"决定要开放"再启用。当前只在最少必要位置做了基础保护（Fernet 加密 + IP 限流 + 自动备份）。

---

## 5. 关键不变量（auto-locked by tests）

`backend/tests/test_invariants.py`（118 项）+ `test_invariants.py` 自身扩写 + `scripts/audit_project.py` 端到端审计：

- 所有改动核心模块后必须 PASS。
- 流程：编辑 → 改对应章节 → `python -m pytest tests/` → 至少本模块绿 → commit。

新增 "audit 类"测试 vs "功能测试"：
- audit 类（防再犯）如不需要平时跑透，可加 `@pytest.mark.audit` marker；
- 功能测试必须 commit 前跑通。

---

## 6. 启动 / 开发 / 部署指南

详见 `README.md` / `dev.bat` / 各子目录的 CLAUDE.md-style 注释。

环境变量单一真相源见 [backend/app/config.py](../backend/app/config.py) 的 Settings 类 (`python -c "from app.config import list_env_keys; print(list_env_keys())"` 列出全部)。

---

## 7. 修订指南

改本文件：
- 当架构图发生变化（如新增 9 个 agent 中的第 10 个、新增路由族）时。
- 当数据契约路径变化时。
- 当新的"加字段流程"出现时。

不修改本文件：
- 修复 bug、调整样式、改 prompt 的细节 — 属 CHANGELOG。
- 实现细节（具体函数、具体 prompt） — 改对应 docstring / 函数头注释。
