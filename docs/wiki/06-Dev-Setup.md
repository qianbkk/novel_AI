# 本地开发与运行

## 环境要求

- Python 3.10+（后端 + 引擎）
- Node.js（前端，Vite 5 / React 18 / TS 5）
- SQLite（内置，无需单独安装）

## 启动步骤

### 1. 后端（端口 8132）

```bash
cd backend
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8132
```

> **多用户认证**：`register`/`login` 接口始终可用，但默认（dev 模式）仍是单租户——不登录也能用全部功能。生产部署需设 `NOVEL_PRODUCTION=1` 强制鉴权（fail-fast 校验 `MASTER_KEY`/`JWT_SECRET`/`ALLOWED_ORIGINS`）。

### 2. 前端（端口 5293）

```bash
cd frontend
npm install
npm run dev
```

前端通过绝对 URL（默认 `http://localhost:8132`，可用 `.env.local` 的 `VITE_API_BASE` 覆盖）直连后端，Vite 不做反向代理。

### 3. 写作引擎依赖

引擎代码内嵌在 `backend/engine/`，作为 `engine.*` Python 包被后端子进程导入，依赖已包含在 `backend/requirements.txt`。若单独跑独立版 `novel_AI/`（见 [07-Standalone-Engine.md](07-Standalone-Engine.md)），需要：

```bash
cd novel_AI
pip install langgraph anthropic httpx jieba
```

打开浏览器访问 `http://localhost:5293`。

## Windows 一键脚本

`dev.bat`（仓库根目录）提供后端+前端的一键启停/状态查看/查日志，日志落在 `.runlogs/`。

## 使用顺序

1. **Provider 页面**（`/settings/providers`）配置模型供应商（Anthropic/DeepSeek/Gemini/Kimi/MiniMax/自定义）
2. **角色配置页面**（`/settings/roles`）为 15 个写作角色绑定 Provider 和模型
3. **新建项目**并完成 10 阶段世界构建向导
4. **写作引擎控制台**绑定 `novel_AI` 工作目录（`NovelAIBinding`）
5. 推送设定（push-concept）→ 生成设定包（planner）→ 拉取设定（pull-setting）→ 黄金三章（bootstrap）→ 选定版本（select）→ 正式写作（run N）→ 导入章节（import-chapters）

## 一键 MVP 脚本（CLI）

`backend/scripts/run_mvp.py` 顺序执行上述 7 步，替代手动点按钮：

```bash
cd backend
# 另一个终端先启动后端
uvicorn app.main:app --reload --port 8132

# 前端新建项目 + 完成 worldbuild，记下 project_id
python -m scripts.run_mvp <project_id>
# 或
python -m scripts.run_mvp <project_id> --chapters 3 --select B
```

流式打印 SSE 日志 + 节点事件，结束时给出摘要和落盘章节列表。

## 常用运维脚本（`backend/scripts/`）

| 脚本 | 用途 |
|------|------|
| `generate_master_key.py` / `rotate_master_key.py` | Fernet `MASTER_KEY` 生成/轮换 |
| `backup_cli.py` | 手动触发数据库快照备份 |
| `export_openapi.py` | 导出 OpenAPI 规范 |
| `audit_project.py` | 端到端不变量审计（历史 5 类跨表 bug 的回归检测） |
| `monitor_run.py` | 实时监控 e2e 测试运行（写 `test_output/*.jsonl`） |
| `cleanup_test_projects.py` | 清理测试项目数据 |
| `strip_chapter_headers.py` / `fixup_50ch_audit.py` | 章节文本一次性清洗修复 |
| `rewrite_length.py` | 用 LLM 把章节字数规整到 1800-2700 |

## novel_AI Bug 修复补丁

`novel_AI/` 目录本身在 `.gitignore` 中，跨机器需手动 apply 补丁。详见 `patches/2026-06-28-novel_ai-mvp-fixes.md`（修复 HTTP 连接池、`node_rewrite` 漏跑 compliance、预算阈值、网络重试）以及 `patches/apply_patches.py`。

## 测试

```bash
cd backend
pytest
```

`backend/tests/` 覆盖鉴权（cookie/隔离/限流）、alembic、对齐（alignment）冒烟测试、冷记忆压缩、长文本端到端、大纲卡片模式、生产加固检查、Tracker 漂移等；`backend/tests/invariants/` 专测跨表数据不变量。引擎自身另有 `engine/tools/system_test.py`（20 项集成测试，含 Mock LLM）和 `acceptance_tests.py`（5 大验收标准）。

## 部署注意事项

> 当前定位是**原型阶段**，生产部署前至少需要：

### Provider API Key 加密

`Provider.api_key` 在 SQLite 中以 Fernet 密文存储，密钥来自环境变量 `MASTER_KEY`。设置了则解密可用；未设置则启动时**临时生成**一个并告警——临时生成的 key 重启后失效（此前加密的数据将无法解密）。

```bash
python -m scripts.generate_master_key
# 输出示例：MASTER_KEY=<base64-urlsafe-44-chars>

export MASTER_KEY='<生成的 key>'
uvicorn app.main:app --host 0.0.0.0 --port 8132 --workers 1
```

⚠️ **多 worker 部署（`--workers N>1`）暂不支持**：每个 worker 进程独立加载 `MASTER_KEY`，目前虽可用，但若未来引入加密缓存或 sticky session，必须保证所有 worker 使用同一个 `MASTER_KEY`。

### 生产模式硬化检查

设置 `NOVEL_PRODUCTION=1` 后启动会 fail-fast 校验：`MASTER_KEY` 必须显式设置、`JWT_SECRET` 必须显式设置、`ALLOWED_ORIGINS` 不能包含 `localhost`/`*`、`RATE_LIMIT_EXEMPT_LOCALHOST` 需关闭。

### 不会提交到仓库的内容

`.env`、运行日志、数据库文件、构建产物、缓存均在 `.gitignore` 中排除。
