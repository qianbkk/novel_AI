# 架构速览

> 这是「5 分钟看清架构」的速览入口；详细请看：
> - [01-Architecture.md](01-Architecture.md) — 三层拓扑 + 进程边界 + 失败模式
> - [02-Backend-API.md](02-Backend-API.md) — FastAPI 路由清单
> - [03-Writing-Engine.md](03-Writing-Engine.md) — 9-Agent orchestrator
> - [05-Data-Model.md](05-Data-Model.md) — SQLAlchemy ORM + 4 套存储
>
> 本页只讲现状。改本文件的触发条件见 [00-Home.md](00-Home.md)。

---

## 1. 一句话

**FastAPI + React 嵌一个 LangGraph 9-Agent 长篇网文写作引擎，前置 10 阶段结构化世界构建，后置图谱+向量混合检索守一致性。dev 模式默认单租户；`NOVEL_PRODUCTION=1` 切换为强制鉴权的多租户模式（Phase 4 起可用）。**

---

## 2. 三层结构

```
┌────────────────────────── 浏览器 (React, Vite :5293) ───────────────────────┐
│ Pages: Dashboard · NewProject · Providers · RoleAssignments ·             │
│   WorldBuild · BridgeConsole · Chapters · RuleCenter · CharacterCard     │
│ api/client.ts: fetch 封装（含 JSON 解析失败脱敏）                            │
│ types.ts ↔ backend Pydantic schema 严格 1:1                                 │
└─────────────────────────────────────────────────────────────────────────────┘
   ↑ VITE_API_BASE (default http://localhost:8132)
   │ SSE (EventSource) + fetch
   ↓
┌───────────────────── FastAPI 后端 (uvicorn :8132) ────────────────────────┐
│                                                                            │
│  lifespan (main.py):                                                      │
│   - NOVEL_PRODUCTION=1 时强制 MASTER_KEY / JWT_SECRET / ALLOWED_ORIGINS  │
│     等已妥善配置 (fail-fast)                                              │
│   - run_migrations (idempotent ALTER TABLE ADD COLUMN)                   │
│   - seed_role_assignments (15 个写作角色种子)                             │
│   - _recover_orphan_bridge_runs (上一轮崩溃的 running 行标 failed)        │
│   - take_all_snapshots (sqlite online backup, 保留 10 份)                 │
│                                                                            │
│  middleware:                                                               │
│   - CORSMiddleware (env ALLOWED_ORIGINS, 默认 localhost:5293)            │
│   - RateLimitMiddleware (env RATE_LIMIT_PER_MINUTE 默认 60;               │
│                          127.0.0.1 / ::1 默认豁免 — 个人使用是摩擦;       │
│                          /auth/login 另有独立的按 (IP,email) 失败限流)    │
│                                                                            │
│  app/api/*: REST 路由（含 auth.py：JWT 注册/登录，HttpOnly Cookie 下发）  │
│  app/auth_scope.py: owner 校验（dev 模式 owner_id IS NULL 全局可见；      │
│                      生产模式强制登录 + 拒绝 NULL-owner 行）              │
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
│  engine/orchestrator.py: 6 节点 LangGraph 状态机 (load_arc_tasks →        │
│    get_next_task → write_pipeline →(不过)→ rewrite →(仍不过)→            │
│    human_escalation；(通过)→ save_and_track，循环至完成)                  │
│  engine/agents/*: 9 个真实实现的 agent                                     │
│    planner / outline (batch|card|talk) / writer / normalizer /            │
│    compliance / checker (主评+2 路交叉) / rewriter (P0/P1/P2) /            │
│    tracker (L2 热冷分层) / summarizer (L5 弧档案) / init_arc              │
│  engine/llm/router.py: 6 provider + mock, length budget 控字数             │
│  engine/memory/manager.py: L2 热冷分层 + 风格样本切换 + 约束过期          │
│  engine/config/*: paths + prompt_templates + power_levels                  │
│  engine/tools/*: bootstrap / budget / scan / fingerprint / exporter...    │
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

## 3. 关键不变量（auto-locked by tests）

- `backend/tests/invariants/test_<domain>.py` 锁定结构与跨存储契约
- `scripts/audit_project.py` 端到端审计
- 流程：编辑 → 更新对应测试 → 按 [`backend/tests/README.md`](../../backend/tests/README.md) 分层运行 → commit

**加 schema 字段的 5 步流程**（防 Planner 输出与消费端字段漂移）：

1. 改 `backend/schema/<schema>.schema.json`
2. 改生成端 prompt（planner.py / stages.py / rewriter.py 等）
3. 改消费端解析（setting_sync.py / chapter_import.py）
4. `python -m scripts.audit_project --strict`（暴露漂移）
5. `pytest backend/tests/invariants -v`（按业务域锁定结构与跨存储不变量）

**自由字段**（个人偏好、不影响引擎逻辑）可以放进 `Project.config_json` 的 freeform 部分，跳过 1-5 步。

---

## 4. 当前真实风险敞口

按实际发生概率与影响排序，不为尚未出现的部署形态预建复杂护栏。

| 风险 | 状态 | 缓解 |
|------|------|------|
| 本地磁盘故障 / 误删 | **真实最大风险** | lifespan sqlite online backup (10 份) 留 24h 内恢复能力。**仍缺**: 定时异地备份（建议配坚果云 WebDAV） |
| 真实生成质量没人验证 | **真实风险** | 真实模型（DeepSeek/Kimi/MiniMax/Anthropic）跑 worldview + 角色卡 + 章节后，质量靠肉眼观察，无自动评估 |
| 跨库一致性窗口 | 受控但需知情 | `novel_assistant.db` 与 `checkpoints.sqlite` 无跨文件事务；靠 `chapter_import.py` 按 `chapter_no` 幂等去重 |

## 已实现 vs 仍冻结的护栏

Phase 4（2026-07-11）起，多用户认证（JWT + bcrypt + HttpOnly Cookie + owner 校验 + 登录限流）已实现，dev 模式默认关闭（单租户），`NOVEL_PRODUCTION=1` 时强制开启。参见 [README.md](../../README.md) "部署" 段落。

仍冻结到"决定要开放"再启用的项：多 worker 部署下的 MASTER_KEY 一致性、分布式任务队列（现为子进程 + DB 状态检查）、密钥管理服务（Vault/KMS）、WAF/DDoS 防护。

---

## 5. 启动 / 开发 / 部署指南

详见 [README.md](../../README.md) / [dev.bat](../../dev.bat) / [06-Dev-Setup.md](06-Dev-Setup.md)。

环境变量单一真相源见 [`backend/app/config.py`](../../backend/app/config.py) 的 Settings 类（`python -c "from app.config import list_env_keys; print(list_env_keys())"` 列出全部）。