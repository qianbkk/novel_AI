# 08 · 前端用户操作 Runbook

> 面向"只通过前端按钮操作"的真实用户。**禁止直接调后端 / 改后端代码 / 改运行进程**——
> 如果前端卡住，应该查日志 + 修代码，让前端能继续走通。

## 1. 启动

### 1.1 Windows 一键脚本（推荐）

```bash
# 仓库根目录
dev.bat           # 交互菜单
dev.bat start-all # 后端 :8132 + 前端 :5293
dev.bat status    # 看运行状态 + /health 探测
dev.bat stop-all  # 停止
```

日志落在 `.runlogs/`，PID 文件记录在 `.runlogs/*.pid`，端口冲突自动判定"我们启动的进程 vs 外部进程"。

> 2026-08-18 修复：launcher 启动 python 时设 `PYTHONIOENCODING=utf-8` + `PYTHONUNBUFFERED=1`，
> 避免 python 按 CP_ACP 写中文日志导致 dev.bat tail 读到乱码。

### 1.2 手动启动（Linux / macOS）

```bash
# 后端
cd backend
set -a; . ./.env; set +a   # 必须 .env env 启动，否则 bridge subprocess 拿不到 LLM_API_KEY
python -m uvicorn app.main:app --host 127.0.0.1 --port 8132 --log-level warning

# 前端（另一个终端）
cd frontend
npm run dev   # 启动在 http://localhost:5293
```

### 1.3 健康检查

- 后端 `http://127.0.0.1:8132/health` 返 `{"status":"ok","db":"ok"}`
- 前端 `http://localhost:5293/` 返 200
- **前端 sidebar 底部有实时状态灯**（2026-08-18 新增）：绿灯 = 后端正常 + 延迟；红灯 = 后端未响应 + 错误详情；黄灯 = 检测中
- **进入需要调 LLM 的页面顶部有 LLM 状态 banner**（2026-08-18 新增）：绿 = 已就绪（mock 或 live 已配 key），红 = 不可用 + 「去配置供应商」引导按钮

## 2. 5 步写作旅程（v1.0 设计核心）

新版前端从"功能清单"改为"用户旅程"——所有项目卡显示 5 步 stepper，点开跳到当前所在步骤对应页。

| 步骤 | 对应页面 | 操作 |
|------|----------|------|
| ① 创建 | `/new` | 填标题 + 题材 + 受众 + 篇幅 → 创建；**创建后自动跳 `/theme`**（Pre-Production） |
| ② 题材画像 + 共性主题 + 黄金三章 + 资料 | `/projects/:id/theme` | 4 个 tab，每个 tab 有「Generate」+「编辑 JSON」+「Save」 |
| ③ 世界构建 | `/projects/:id/worldbuild` | 点「开始构建」，等 10 阶段 SSE 跑完 |
| ④ 大纲 | `/projects/:id/outline` | 「生成新大纲」填弧名 + 弧目标 + 章节数；支持重新生成 + 编辑 |
| ⑤ 写章节 | `/projects/:id/bridge` | push-concept → planner → pull-setting → bootstrap → select → run N → import-chapters |

每完成一步，Dashboard 项目卡的 stepper 会把该步打 ✓ + 跳到下一步高亮。

## 3. Provider 配置（必做，否则 LLM 操作全失败）

v1.0.1 起，进入任意需要 LLM 的页面都会**自动探测后端 LLM 状态**。两种显示：

**绿（已就绪）**
- mock 模式：内置示例，无需 API key
- live 模式：所有角色路由可达，或至少全局 provider 可用

**红（不可用）**
- 列出每个角色失败的具体原因（如"未配置 deepseek 的 API key"）
- 「去配置供应商 →」按钮直接跳 `/settings/providers`
- 不可用时 `WorldBuild` 的「开始构建」按钮自动禁用；其他页操作时会先预检

### 3.1 Provider CRUD

- 前端 `/settings/providers`：填 name / type / base_url / api_key / default_model
- 填 key 后会自动加密存到 SQLite（Fernet + MASTER_KEY）
- 「立即测试」按钮（2026-08-18 端点就绪，前端接入待续）：实际发 ping 验证连通性 + key 正确性

### 3.2 角色分配

- 前端 `/settings/roles`：15 个角色绑定 Provider + 可选模型覆盖
- 不绑也能跑：fallback 到 `settings.llm_provider`

## 4. 真实 LLM 跑完整流程

### 4.1 创建项目 + Pre-Production + 世界构建

- 前端 `/new` 填表提交 → 跳 `/projects/:id/theme`
- `/theme` 4 个 tab 按需生成（可跳过但会影响后续质量）：
  - ① 题材画像：6 个男频题材模板（玄幻/仙侠/都市/历史/军事/科幻）
  - ② 共性主题：用户初始概念 + 题材画像 → theme_statement / expectation_arc / resonance_anchors
  - ③ 黄金三章：ch1 锚定 → ch2 问题 → ch3 翻转（hook_type 严格 7 个合法之一）
  - ④ 资料助手：按 research_strength 三档分流（历史强 / 玄幻弱）
- `/worldbuild` 点「开始构建」：
  - 10 阶段：parse_config / world_basics / plot_skeleton / characters / relations / foreshadowing / map / factions_power / currency_special / consistency_check
  - SSE 流实时进度
  - 状态变化：draft → worldbuilding → ready
  - 耗时分：每阶段 30-60s（真 LLM），10 阶段共 5-10 分钟

### 4.2 引擎桥接

- 前端 `/projects/:id/bridge` 点 push-concept → 状态 running → completed
- 依次点：planner / pull-setting / bootstrap（3 候选生成 + 选 A） / init_arc
- 每次完成后 SSE 事件流会有 complete 事件
- **v1.0.1 修复**：跑之前预检 LLM 状态；不可用时直接拒绝 + 引导去配置

### 4.3 跑 30 章

- 前端 bridge 页输入 "30" + 点 run
- 关键观察点：
  - SSE 流应该持续输出 `[hh:mm:ss] Ch0001 | ...` 等日志
  - 每 1-3 min 完成一章
  - 30 章真实跑完预算约 $0.6-1.0
  - 预算超 0.95 × budget_limit_usd 时 BUDGET_HARD 触发停

### 4.4 导入章节到 DB

- 前端 bridge 页点 `import-chapters`
- 把 ch_NNNN.txt + meta.json 写入 `Chapter` 表
- 同时为每章匹配 character 边（关系抽取）

## 5. 故障排查

### 5.1 进入页面就看到「LLM 未配置」红色 banner
- 原因：后端未配 LLM provider 或 key 缺失
- 修：点 banner 上的「去配置供应商 →」按钮 → `/settings/providers` 填 key

### 5.2 Sidebar 底部后端状态灯红色
- 原因：后端没起来 / 端口冲突 / 网络问题
- 修：点 ▾ 展开看错误详情；终端跑 `dev.bat start-all` 或手动启动

### 5.3 dev.bat tail 日志中文乱码
- 2026-08-18 已修：launcher 设 `PYTHONIOENCODING=utf-8` + `PYTHONUNBUFFERED=1`
- 如果老日志已写坏，重启即可重新写入正常

### 5.4 后端启动报 LLM_API_KEY 未设置
- 原因：uvicorn 没读 .env
- 修：后端代码已加 worker 顶部 .env loader 兜底；重启后端即可

### 5.5 章节标题是 `{"title": "..."}` JSON 字面量
- 原因：LLM 返 JSON 包装，writer 解析失败
- 修：5 步全链路修复已 commit（d5d4bd9 / 6ffdb67）；重启后端 + reimport

### 5.6 characters 表「林渊」重复 2 行
- 原因：preset_worldbuild 阶段没去重
- 修：stages.py 加 seen_names 集合去重（7f72545 + bb62a0c）
- 老数据：用 SQL 一次性清理

### 5.7 30 章跑到一半 ch8+ 出现 0 字占位
- 原因：MiniMax Token Plan 速率限制（status_code 2062）
- 当前修：orchestrator 0 字节时**不**落盘 ch_NNNN.txt（e5dc1ff）
- 下次跑：要么升 Token Plan 要么 audit_mode=lite

### 5.8 前端 /characters 列表显示"无内容"
- 原因：API 没返 personality_summary 字段
- 修：CharacterSummaryOut schema + 前端 type 加字段（bb62a0c）

### 5.9 bridge.run 返 409 "bridge run already active"
- 原因：上次的 run 还在 running
- 修：用 SQL 把 bridge_runs.status='running' 改成 'failed'
  ```sql
  UPDATE bridge_runs SET status='failed', exit_code=-1 WHERE status='running';
  ```

### 5.10 v1.0 pre-production 文件加载失败（看不到静默）— 2026-08-18 修复
- 之前：5 个 `load_*` 函数 `except Exception: return None` 无 log；磁盘损坏 / 权限错误 / 编码错误都静默吞掉
- 现在：每次失败都有 `_log.exception` traceback 写到 `backend/logs/novel_ai.log`
- 同样修了 6 个 `_parse_llm_json` wrapper（重复包装 + 吞 import error）+ 3 个 backup-rename fallback（数据丢失风险点）

## 6. 看日志

### 6.1 实时后端日志

- `backend/logs/novel_ai.log`（5MB×5 滚动）
- `tail -f backend/logs/novel_ai.log | grep -E "ERROR|WARNING"`

### 6.2 每次 run 的 SSE 事件

- `docs/runs/30ch-real-YYYY-MM-DD/<command>_sse.jsonl`
- 用浏览器 developer tools 看 SSE 流（前端 F12 → Network → EventStream）

### 6.3 引擎落盘产物

- `engine/output/chapters/ch_NNNN.txt` — 章节正文
- `engine/output/chapters/ch_NNNN_meta.json` — 章节元数据
- `engine/output/orchestrator_state.json` — 当前 orchestrator 状态
- `engine/memory/l2/<project_id>_memory.json` — L2 长期记忆

## 7. 不应该做的事

- ❌ 直接改后端 DB 表结构
- ❌ 直接调 engine.tools.select_version / init_arc（绕过 bridge）
- ❌ 改 Pydantic schema 不写迁移
- ❌ 加新依赖 / 改环境变量名（破坏 CLAUDE.md 约束）
- ❌ 写 phase/iteration 报告（已改用 wiki/09 架构审查产物）
- ❌ 把 LLM 调用失败的 except Exception 改回 `pass`（违反 CLAUDE.md「失败要响亮」）

## 8. 应该做的事

- ✅ 通过前端按钮操作，让 bridge.run 走完整 pipeline
- ✅ 查 backend/logs/novel_ai.log 找 ERROR / WARNING
- ✅ 查 docs/runs/ 下次跑报告看具体问题
- ✅ 修代码时一个 commit 一个聚焦改动
- ✅ 修完跑 `pytest backend/tests/ --ignore=backend/tests/invariants` 验证
- ✅ 跑 `npm --prefix frontend run build` 验证前端编译
- ✅ v1.0 新增 silent fail 修复后跑对应单元测试（如 `test_provider_health_2026_08_18.py`）