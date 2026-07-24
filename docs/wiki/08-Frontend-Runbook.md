# 08 · 前端用户操作 Runbook

> 面向"只通过前端按钮操作"的真实用户。**禁止直接调后端 / 改后端代码 / 改运行进程**——
> 如果前端卡住，应该查日志 + 修代码，让前端能继续走通。

## 1. 启动

### 1.1 后端
```bash
cd backend
# 必须用 .env env 启动（否则 bridge subprocess 拿不到 MINIMAX_API_KEY）
set -a; . ./.env; set +a
python -m uvicorn app.main:app --host 127.0.0.1 --port 8132 --log-level warning
```

### 1.2 前端
```bash
cd frontend
npm run dev   # 启动在 http://localhost:5293
```

### 1.3 健康检查
- 后端 `http://127.0.0.1:8132/health` 返 `{"status":"ok","db":"ok"}`
- 前端 `http://localhost:5293/` 返 200

## 2. 30 章真实 LLM 跑完整流程（前端视角）

### 2.1 创建项目 + 世界构建（10 阶段 worldbuild）
- 前端：`/projects/new` 填表提交
- 后端自动跑 10 阶段：parse_config / world_basics / plot_skeleton / characters / relations / foreshadowing / map / factions_power / currency_special / consistency_check
- **状态变化**：draft → worldbuilding → ready
- **耗时分**：每阶段 30-60s（真 LLM），10 阶段共 5-10 分钟
- **若卡在某阶段**：看后端 `backend/logs/novel_ai.log`，搜 `stage_xxx` 找错

### 2.2 配置 Provider（MiniMax-M3）
- 前端：`/providers` 填 API key + base_url
- 填：
  - name: `MiniMax-M3`
  - provider_type: `minimax`
  - api_base: `https://api.minimaxi.com/v1`
  - api_key: `<你的 MiniMax key>`
  - default_model: `MiniMax-M3`

### 2.3 角色分配（RoleAssignment）
- 前端：`/role-assignments` 看到 15 个角色
- 把所有 15 个绑到上一步创建的 MiniMax Provider

### 2.4 引擎桥接
- 前端：`/projects/{pid}/bridge` 点 push-concept → 看到状态 running → completed
- 依次点：planner / pull-setting / bootstrap（3 候选生成 + 选 A） / init_arc
- 每次完成后 SSE 事件流会有 complete 事件

### 2.5 跑 30 章
- 前端：`bridge` 页输入 "30" + 点 run
- **关键观察点**：
  - SSE 流应该持续输出 `[hh:mm:ss] Ch0001 | ...` 等日志
  - 每 1-3 min 完成一章
  - 30 章真实跑完预算约 $0.6-1.0（MiniMax Token Plan 余额需 ≥ $1）
  - 预算超 0.95 × budget_limit_usd 时 BUDGET_HARD 触发停

### 2.6 导入章节到 DB
- 前端：bridge 页点 `import-chapters`
- 把 ch_NNNN.txt + meta.json 写入 `Chapter` 表
- 同时为每章匹配 character 边（关系抽取）

## 3. 故障排查

### 3.1 后端启动报 MINIMAX_API_KEY 未设置
- 原因：uvicorn 没读 .env
- 修：后端代码已加 worker 顶部 .env loader 兜底；重启后端即可

### 3.2 章节标题是 `{"title": "..."}` JSON 字面量
- 原因：LLM 返 JSON 包装，writer 解析失败
- 修：5 步全链路修复已 commit（d5d4bd9 / 6ffdb67）；重启后端 + reimport

### 3.3 characters 表「林渊」重复 2 行
- 原因：preset_worldbuild 阶段没去重
- 修：stages.py 加 seen_names 集合去重（7f72545 + bb62a0c）
- 老数据：用 SQL 一次性清理

### 3.4 30 章跑到一半 ch8+ 出现 0 字占位
- 原因：MiniMax Token Plan 速率限制（status_code 2062）
- 当前修：orchestrator 0 字节时**不**落盘 ch_NNNN.txt（e5dc1ff）
- 下次跑：要么升 Token Plan 要么 audit_mode=lite

### 3.5 前端 /characters 列表显示"无内容"
- 原因：API 没返 personality_summary 字段
- 修：CharacterSummaryOut schema + 前端 type 加字段（bb62a0c）

### 3.6 bridge.run 返 409 "bridge run already active"
- 原因：上次的 run 还在 running
- 修：用 SQL 把 bridge_runs.status='running' 改成 'failed'
  ```sql
  UPDATE bridge_runs SET status='failed', exit_code=-1 WHERE status='running';
  ```

### 3.7 Dashboard 看不到"正在跑"的项目（2026-07-24 修复）
- **修后行为**：项目卡片右下角显示 **⟳ + 命令名 + 呼吸动画**（`⟳ 生成设定包` / `⟳ 写10章` 等），点击直接跳 BridgeConsole 看 SSE 实时日志
- **实现**：后端 `/projects` endpoint 现在附带 `active_run_command/status/id/started_at` 字段（每项目最新一条 pending/running 的 BridgeRun）
- **触发时机**：`POST /bridge/run` 创建 `BridgeRun(status="pending"|"running")` 即生效；`done` / `failed` 后 badge 自动消失
- **修前症状**：旧版本 Dashboard 只看 `Project.status`，bridge.run 跑时 status 不变，用户看不到 ⟳ 提示

## 4. 看日志

### 4.1 实时后端日志
- `backend/logs/novel_ai.log`（5MB×5 滚动）
- `tail -f backend/logs/novel_ai.log | grep -E "ERROR|WARNING"`

### 4.2 每次 run 的 SSE 事件
- `docs/runs/30ch-real-YYYY-MM-DD/<command>_sse.jsonl`
- 用浏览器 developer tools 看 SSE 流（前端 F12 → Network → EventStream）

### 4.3 引擎落盘产物
- `engine/output/chapters/ch_NNNN.txt` — 章节正文
- `engine/output/chapters/ch_NNNN_meta.json` — 章节元数据
- `engine/output/orchestrator_state.json` — 当前 orchestrator 状态
- `engine/memory/l2/<project_id>_memory.json` — L2 长期记忆

## 5. 不应该做的事

- ❌ 直接改后端 DB 表结构
- ❌ 直接调 engine.tools.select_version / init_arc（绕过 bridge）
- ❌ 改 Pydantic schema 不写迁移
- ❌ 加新依赖 / 改环境变量名（破坏 CLAUDE.md 约束）
- ❌ 写 phase/iteration 报告（已改用 wiki/07 + RUN_DIR 报告）

## 6. 应该做的事

- ✅ 通过前端按钮操作，让 bridge.run 走完整 pipeline
- ✅ 查 backend/logs/novel_ai.log 找 ERROR / WARNING
- ✅ 查 docs/runs/ 下次跑报告看具体问题
- ✅ 修代码时一个 commit 一个聚焦改动
- ✅ 修完跑 `pytest backend/tests/ --ignore=backend/tests/invariants` 验证
- ✅ 跑 `npm --prefix frontend run build` 验证前端编译
