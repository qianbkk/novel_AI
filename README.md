# novel_AI

novel_AI：一个用多 Agent 协作写长篇网文的工程。

FastAPI + React Web 框架内嵌一个 LangGraph 多 Agent 写作引擎。前端点点按钮，9 个写作 Agent（Planner / Writer / Normalizer / Compliance / Checker×3 / Rewriter / Outline / Summarizer / Tracker）协同生成设定、规划章节、逐章写作、质量评审与重写，章节自动入库。

仓库目录：

- `backend/`：FastAPI 后端，提供项目管理、世界构建、Provider/角色配置、写作引擎桥接、章节导入与检索。
- `frontend/`：React + TypeScript + Vite 前端，提供项目页面、Provider 管理、角色配置和写作引擎控制台。
- `docs/`：项目文档与可视化页面（含自解释指南 HTML）。
- `patches/`：写作引擎的修复 apply 指南，跨机器需手动 apply。

## 本地运行

后端：

```bash
cd backend
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8132
```

> **多用户认证**：v2 起 `register` / `login` 端点可用，但 **dev 模式（默认）仍是单租户**——不登录也能用所有功能，方便本地原型。
> 真要多用户隔离时设 `NOVEL_PRODUCTION=1` 启动后端（fail-fast 强制鉴权）。
> 详见 `docs/superpowers/plans/2026-07-11-phase3-launch-trigger.md`。

前端：

```bash
cd frontend
npm install
npm run dev
```

写作引擎依赖（NovelAI 项目根目录）：

```bash
cd novel_AI
pip install langgraph anthropic httpx jieba
```

打开 `http://localhost:5293`。

## 使用顺序

1. 在 Provider 页面配置模型供应商。
2. 在角色配置页面为 15 个写作角色绑定 Provider 和模型。
3. 新建项目并完成世界构建。
4. 在写作引擎控制台绑定 `novel_AI` 目录。
5. 推送设定、生成设定包、运行章节写作，并导入章节。

## 一键 MVP 脚本（CLI）

替代手动点 4 个按钮，用 `backend/scripts/run_mvp.py` 顺序跑：push-concept → planner → pull-setting → bootstrap → select → run N → import-chapters。

```bash
cd backend
# 1. 启动后端（另一个终端）
uvicorn app.main:app --reload --port 8132

# 2. 在 frontend 新建项目 + 完成 worldbuild（10 阶段），记下 project_id
# 3. 跑 MVP（默认写 1 章，选版本 A）
python -m scripts.run_mvp <project_id>
# 或：python -m scripts.run_mvp <project_id> --chapters 3 --select B
```

流式打印 SSE 日志 + node 事件，结束给摘要 + 列落盘章节。

## novel_AI Bug 修复

`novel_AI/` 在 `.gitignore`，跨机器需手动 apply。详见 `patches/2026-06-28-novel_ai-mvp-fixes.md`（修复 HTTP 连接池、node_rewrite 漏 compliance、预算阈值、网络重试）。

## 注意

`.env`、运行日志、数据库、构建产物和缓存不会提交到仓库。

## 部署

> ⚠️ 当前是原型阶段。生产部署**至少**要做以下配置：

### 1. Provider API Key 加密（必须）

`Provider.api_key` 列在 SQLite 中以 **Fernet ciphertext** 存储，密钥来自环境变量 `MASTER_KEY`。
设了 `MASTER_KEY` → 用它解密；没设 → 启动时**临时生成**一个 + 警告。临时生成的 key 重启后会失效（已加密的 key 解不开）。

**生产部署务必设置：**

```bash
# 生成一个新的 MASTER_KEY
python -m scripts.generate_master_key
# 输出示例：MASTER_KEY=<base64-urlsafe-44-chars>

# 启动后端时注入
export MASTER_KEY='<上面生成的 key>'
uvicorn app.main:app --host 0.0.0.0 --port 8132 --workers 1
```

⚠️ **多 worker 部署（gunicorn / uvicorn --workers N>1）暂不支持**：每个 worker 进程独立加载 MASTER_KEY，目前 OK；但若未来做加密缓存或 sticky session，**必须**保证所有 worker 用同一个 MASTER_KEY。

### 2. CORS 收紧（必须）

默认 `ALLOWED_ORIGINS=http://localhost:5293`（前端 dev 端口）。
部署前端到 `https://your-frontend.example.com` 时：

```bash
export ALLOWED_ORIGINS='https://your-frontend.example.com,https://www.your-frontend.example.com'
```

### 3. 端口与绑定（推荐）

- 后端：`uvicorn ... --host 0.0.0.0 --port 8132`，前面套 nginx/Caddy 反代 + HTTPS
- 前端：`npm run build` 后 `dist/` 是静态文件，nginx 直接 serve

### 4. 反代场景下的速率限制 + IP 防伪造（必须）

后端有内存速率限制中间件（默认 60 次/分钟/IP，仅写端点）。
**反代部署必须设 `ALLOWED_PROXIES`**（逗号分隔 IP/CIDR），否则攻击者
伪造 `X-Forwarded-For` header 绕过限流：

```bash
# nginx 反代在 10.0.0.5
export ALLOWED_PROXIES='10.0.0.5,10.0.0.6'
```

nginx 端同步设：
```nginx
proxy_set_header X-Forwarded-For $remote_addr;  # 真实 IP，不是 XFF 链
```

直接暴露 uvicorn 时不要配 `ALLOWED_PROXIES`（默认 fallback 到 `request.client.host`）。

### 5. MASTER_KEY 轮换（运维）

定期 / 泄漏应急时轮换 MASTER_KEY：

```bash
# 1. 生成新 key（先备份旧 key 在 env 里的值）
python -m scripts.generate_master_key  # 旧 key 仍在 env

# 2. 跑轮换（--dry-run 先演练）
python -m scripts.rotate_master_key --new-key <new_44_char_key> --dry-run

# 3. 真跑
python -m scripts.rotate_master_key --new-key <new_44_char_key>

# 4. 把新 key 注入 K8s Secret / .env / 系统环境变量，移除旧 key
# 5. 重启后端 + 验证 /providers 端点能正常解密
```

**关键**：必须先备份 DB（`cp data/novel_assistant.db data/novel_assistant.db.bak`），
并在后端**停机期间**运行（避免 in-flight read 拿旧 key / 新 write 拿新 key 的竞态）。

### 6. OpenAPI 自动导出（CI 集成）

后端 schema 经常变，手工维护 `frontend/openapi.json` 会漂移。
CI 集成：

```bash
# 启动后端后
python -m scripts.export_openapi  # 写到 frontend/openapi.json（已 gitignored）
# 或指定 URL
python -m scripts.export_openapi --url http://localhost:8132

# GitHub Actions 示例
- name: Export OpenAPI spec
  run: |
    uvicorn app.main:app --port 8132 &
    sleep 5
    python -m scripts.export_openapi
```

### 7. Mock provider（无需 API key 跑通全流程）

设 `NOVEL_ENGINE_MOCK=1` 让所有 9 个 agent 走 mock provider（无需 API key，
返回 schema 化固定响应）：

```bash
NOVEL_ENGINE_MOCK=1 uvicorn app.main:app --port 8132
```

适用：CI / 单元测试 / demo / 本地没配 API key 时。

### 8. 生产模式强制检查（fail-fast）

设 `NOVEL_PRODUCTION=1` 启用生产模式：启动时强制要求 `MASTER_KEY` 已设置，
否则进程立即退出（防止忘设 key → 数据损坏）：

```bash
export NOVEL_PRODUCTION=1
export MASTER_KEY='<44 字符 base64-urlsafe>'
uvicorn app.main:app --port 8132
```

### 9. 启动时自动迁移

后端 lifespan handler 启动时会自动跑 `run_migrations()`（给已有表加新列）。
SQLite 适合原型；生产建议迁 PostgreSQL（改 `database_url` 即可）。

### 10. 不在这次范围内的项

- 多用户认证（当前是单租户原型）
- 分布式任务队列（现在是进程内 lock + SQLite）
- 密钥管理服务（Vault / AWS KMS 之类）
- WAF / DDoS 防护（反代前面套 Cloudflare）