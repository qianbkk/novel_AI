# novel-assistant × novel_AI 融合实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (- [ ]) syntax for tracking.

**Goal:** 将 novel-assistant（Web 世界构建 UI）和 novel_AI（LangGraph 多 Agent 写作引擎）融合成一个完整的 AI 辅助小说创作项目。

**Architecture:** novel_AI 作为子进程调起（源码不做修改），通过 bridge/ 桥接模块通信。API key 和模型路由通过统一的 Provider/RoleAssignment 系统管理（15 个角色扁平列表），存储在 providers 和 role_assignments 表中。

**Tech Stack:** FastAPI / SQLAlchemy / SQLite / React + TypeScript + Vite / Python subprocess (novel_AI)

---

### Task 1: 创建角色注册表 + 启动种子数据

**Files:**
- Create: backend/app/bridge/role_registry.py
- Modify: backend/app/main.py

- [ ] Step 1: 创建 role_registry.py，包含全部 15 个角色（structured_logic, creative_detail, consistency_check, orchestrator, planner, outline, writer, normalizer, compliance, checker_main, checker_cross1, checker_cross2, rewriter, tracker, summarizer）
- [ ] Step 2: 在 main.py 添加 @app.on_event("startup") 种子数据函数，启动时检查 role_assignments 表，为每个不存在的 role_key 插入一条空记录
- [ ] Step 3: 提交

---

### Task 2: 更新 Pydantic schema

**Files:**
- Modify: backend/app/schemas.py

- [ ] Step 1: ProjectOut 追加 budget_limit_usd: Optional[float] 和 novel_ai_status: str
- [ ] Step 2: 追加 ProviderCreate, ProviderOut, RoleAssignmentOut（含 label/provider_name/provider_type）, RoleAssignmentUpdate, BridgeRunRequest, BridgeRunOut, ReviewRequest
- [ ] Step 3: 提交

---

### Task 3: Provider CRUD API

**Files:**
- Create: backend/app/api/providers.py

- [ ] Step 1: 实现 GET /providers - 列表（按 created_at 排序）
- [ ] Step 2: 实现 POST /providers - 新增，验证 provider_type 在允许列表中
- [ ] Step 3: 实现 PUT /providers/{id} - 全字段覆盖修改
- [ ] Step 4: 实现 DELETE /providers/{id} - 删除前把引用该 provider 的 role_assignments.provider_id 置空，静默处理
- [ ] Step 5: 提交

---

### Task 4: RoleAssignment API

**Files:**
- Create: backend/app/api/role_assignments.py

- [ ] Step 1: 实现 GET /role-assignments - 从 role_registry.py 读 label，join Provider 表带回 provider_name/provider_type
- [ ] Step 2: 实现 PUT /role-assignments/{role_key} - body: {provider_id, model_override}
- [ ] Step 3: 提交

---

### Task 5: 创建 Bridge 执行器（env_writer + invoke）

**Files:**
- Create: backend/app/bridge/env_writer.py
- Create: backend/app/bridge/invoke.py

- [ ] Step 1: env_writer.py - 从 Provider 表读取配置，按 PROVIDER_ENV_KEYS 映射写入 novel_AI/.env（处理 minimax group_id、custom api_base/model、代理配置）
- [ ] Step 2: invoke.py - bootstrap 脚本 + asyncio.create_subprocess_exec 调起 novel_AI run.py，注入角色路由覆写（在 import api_client 之前写 os.environ，确保 MODEL_ROUTES 被正确覆写）
- [ ] Step 3: 提交

---

### Task 6: 创建控制面 Reports 模块

**Files:**
- Create: backend/app/bridge/reports.py

- [ ] Step 1: read_status - 读 output/orchestrator_state.json，不存在时返回友好提示
- [ ] Step 2: read_pending - 取 human_pending 列表
- [ ] Step 3: read_budget_log - 按行解析 logs/budget_log.jsonl
- [ ] Step 4: apply_review - 读/写 orchestrator_state.json，支持 accept/reject/edit（edit 时写回章节文件）
- [ ] Step 5: 提交

---

### Task 7: 创建 Bridge API 端点

**Files:**
- Create: backend/app/api/bridge.py

- [ ] Step 1: POST /projects/{id}/bridge/run - 校验绑定、并发约束（409）、写 .env、组装 role_overrides、起 BridgeRun 记录、异步调 invoke、自动串联（planner→pull_setting、run→import_chapters）
- [ ] Step 2: GET /projects/{id}/bridge/stream - SSE 日志流，复用 asyncio.Queue 模式
- [ ] Step 3: POST .../push-concept 和 .../pull-setting
- [ ] Step 4: POST .../import-chapters
- [ ] Step 5: GET .../status, /pending, /budget 和 POST .../review
- [ ] Step 6: 提交

---

### Task 8: 更新 main.py 注册新路由

**Files:**
- Modify: backend/app/main.py

- [ ] Step 1: 从 app.api 导入 providers, role_assignments, bridge 路由并 include_router
- [ ] Step 2: 确保 database.py 已导出 get_db（sessionmaker 依赖注入）
- [ ] Step 3: 提交

---

### Task 9: 更新前端类型定义

**Files:**
- Modify: frontend/src/types.ts

- [ ] Step 1: 追加 Provider, RoleAssignment, BridgeRun, BridgeLogLine, BridgeStatus, BridgePendingItem 接口
- [ ] Step 2: 提交

---

### Task 10: 更新前端 API Client

**Files:**
- Modify: frontend/src/api/client.ts

- [ ] Step 1: 追加 Provider/RoleAssignment/Bridge 相关 API 方法
- [ ] Step 2: 提交

---

### Task 11: 创建 Providers 页面

**Files:**
- Create: frontend/src/pages/Providers.tsx

- [ ] Step 1: 实现 Provider 列表表格 + 新增/编辑 Modal 表单 + 删除确认
- [ ] Step 2: 提交

---

### Task 12: 创建 RoleAssignments 页面

**Files:**
- Create: frontend/src/pages/RoleAssignments.tsx

- [ ] Step 1: 实现 15 行角色配置表格 + 行内编辑（Provider 下拉 + 模型覆盖输入）
- [ ] Step 2: 提交

---

### Task 13: 创建 BridgeConsole 控制台页面

**Files:**
- Create: frontend/src/pages/BridgeConsole.tsx

- [ ] Step 1: 实现按钮组（10 个命令）+ SSE 实时日志区 + 控制面结果面板
- [ ] Step 2: 提交

---

### Task 14: 更新前端路由和导航

**Files:**
- Modify: frontend/src/App.tsx

- [ ] Step 1: 追加 /settings/providers, /settings/roles, /projects/:projectId/bridge 路由
- [ ] Step 2: 导航栏追加 Provider 和 角色配置 链接
- [ ] Step 3: 编译验证 (npx tsc --noEmit)
- [ ] Step 4: 提交

---

### Task 15: 安装 novel_AI 依赖

- [ ] Step 1: 复制 novel_AI/.env.template -> novel_AI/.env（如果 .env 不存在）
- [ ] Step 2: pip install langgraph anthropic httpx jieba
- [ ] Step 3: 验证 python -c "from api_client import MODEL_ROUTES; print(len(MODEL_ROUTES), 'agents')"