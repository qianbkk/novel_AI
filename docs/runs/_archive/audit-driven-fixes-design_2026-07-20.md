# 2026-07-20 审计驱动修复 — Design Spec

> 对应 commit `1b29503`（HEAD = `feat(rewrite): 单章改写 API`）。
> 本 spec 基于两份审计报告的交叉核对结果（已在源码 `file:line` 级别核实），按用户确认范围对 12 项问题进行最小化修复。
> 范围：核心生成质量与数据正确性 / 前后端契约与页面功能 / 安全与可靠性（不动表结构）。
> 不做：Provider/RoleAssignment owner 字段新增、Phase 1 全局共享配置结构性变更、核心 Agent prompt 调整、LangGraph 拓扑变更、新增依赖/环境变量/数据库表/公共 API。

---

## 1. 背景

最近三天（commit 80deb5c/1b29503 等）上线了「已有小说上传」特性族（import-text / extract-setting / chapter-rewrite），并完善了 push-concept 的 snapshot 合并。两份审计确认了 12 项问题，其中 8 项属于"已上线但未防护"的真实风险。

按 CLAUDE.md 长期约束：

- 必须先有能复现问题的测试再做实现
- 复用现有 Schema、Provider 路由、角色分配、预算、质量门、分层记忆与 BridgeRun，不建立平行系统
- 保持不变量：所有 project-scoped API 必须 ownership 校验；敏感信息不得写入日志/SSE/错误响应；引擎落盘原子写、不覆盖已完成章节
- Windows 是主要开发环境，文件读写显式 UTF-8
- 不创建 phase/iteration/audit 报告（行为更新到已有主题文档）

因此本 spec 全部围绕"最小修复 + 测试 + 文档更新"展开。

---

## 2. 修复项清单与设计

每个修复项独立成节，按"问题 / 现状 / 修复 / 测试 / 风险"五段描述。

### 2.1 SSE 错误事件向前端泄漏 traceback

- **文件**：`backend/app/api/bridge.py:388-389`、`:417-420`
- **现状**：
  - `_drain_stdout` 线程捕获 `loop_exc` 后，将 `traceback.format_exc()` 完整堆栈放入 queue。
  - `/bridge/stream` 端点 `event_generator()` 收到 payload 后直接 `json.dumps(payload)` 推给前端。
  - 堆栈可能含绝对路径、SQL 语句片段、环境变量名、内部模块结构。
- **修复**：
  1. `_drain_stdout` 端：仍 `log.exception(...)`（保留运维记录），但 queue payload 不再带 `traceback` 字段；`message` 截取前 200 字符并以"内部错误"为前缀。
  2. `/bridge/stream` 端：在 `event_generator()` 转发前对 `payload.event == "error"` 做一次白名单：只透传 `event` 与 `message`，丢弃其余字段（防御性，未来新增字段时同样生效）。
  3. `BridgeRun.stdout_text` 仍保留后端日志（运维诊断用），但不再经 SSE 暴露。
- **测试**：
  - 新增 `backend/tests/api/test_bridge_stream_error_sanitization.py`：
    - 构造一个触发 `loop_exc` 的 mock 引擎子进程（注入 `proc.stdout.readline` 抛错），断言 SSE payload 中无 `traceback` 键，且 `message` 长度 ≤ 250。
  - 行为回归：现有 `test_bridge_stream.py` 应继续通过（happy path 不变）。
- **风险**：
  - 极低。修改仅影响错误路径，不动正常 stdout/log 事件。
  - 前端若依赖 traceback 做调试，需要改用 `GET /bridge/runs/{run_id}` 拉完整 stdout（已存在端点，运维工具而非用户可见）。

### 2.2 `_rebuild_chapter_character_edges` N+1 查询

- **文件**：`backend/app/novel_extract.py:503-525`
- **现状**：三层循环 + 每次匹配后 `db.query(ChapterCharacter).filter_by(chapter_id, character_id).first()` 存在性检查。
- **修复**：
  1. 入口处一次性预加载已有边：`existing_edges = {(cc.chapter_id, cc.character_id) for cc in db.query(ChapterCharacter).join(Chapter).filter(Chapter.project_id == project_id).all()}`。
  2. 循环中命中后用 `if (ch.id, c.id) not in existing_edges:` 判断；新增后同步加入 `existing_edges`。
  3. 不引入新索引（ChapterCharacter 已有 PK），不引入新依赖。
  4. docstring 更新为「O(N×M) 内存比对，DB 写入仍是 O(新边数)」并解释 1 + 1 次查询而非 N×M 次。
- **测试**：
  - 新增 `backend/tests/services/test_novel_extract_rebuild_edges.py`：
    - 准备 20 章 × 5 角色 fixture；调用 `_rebuild_chapter_character_edges`；用 `query_counter` 或 SQLAlchemy event listener 断言 query 总数 ≤ 5（基线：1 章 + 1 角色 + 1 边预加载 + 1 commit）。
    - 重复调用幂等：第二次返回 0 且不报 unique 冲突。
- **风险**：
  - 内存中保存 set，边数极多时（≥10k）内存压力上升。当前 300 章 × 30 角色 ≈ 9k 上限可接受；如未来有更大项目，可改为分批处理（不在本次范围）。
  - 行为完全等价：仍按"章节正文中是否包含角色名"判定，匹配规则不变。

### 2.3 `rewrite_candidates.json` 非原子写 + TOCTOU

- **文件**：`backend/app/chapter_rewrite.py:199-277`
- **现状**：
  - 候选文件用 `tmp_path.replace()` 原子写（已正确）。
  - 索引文件 `index_path.write_text(...)` 非原子写（line 274），进程中断 → 索引损坏。
  - `_existing_labels` 扫磁盘，`_next_label` 选下一个；同章并发两个请求可能分配到同 label → 第二个 `tmp_path.replace()` 覆盖第一个候选。
- **修复**：
  1. 索引文件改用 `tmp_path = index_path.with_suffix(index_path.suffix + ".tmp")` + `tmp_path.write_text(json.dumps(...), encoding="utf-8")` + `tmp_path.replace(index_path)`，复用 `engine.utils.atomic_write_json`（已在 `setting_sync.py:29` 使用）。
  2. 同章并发改写：在 `rewrite_chapter` 入口加 DB 级互斥 —— 利用 `BridgeRun` 风格做不到（rewrite 不经 BridgeRun）。最小实现：增加一个轻量表锁替代物 `RewriteLock`（仅进程内 threading.Lock，按 `(project_id, chapter_no)` 分桶），存到模块级 dict 中。
     - 不引入数据库表 / 新依赖 / 新公共 API。
     - 单进程内单章并发请求串行；跨 worker 场景由后端 worker=1 部署不变量保证（CLAUDE.md 接受）。
  3. 文档更新：在 docstring 写明并发模型（单进程内互斥，跨 worker 不保护）与 fallback 提示「多次重试 / 复核版 selector」。
- **测试**：
  - 新增 `backend/tests/services/test_chapter_rewrite_atomic.py`：
    - 写入 50 次连续 rewrite 同一章，每次分配不同 label，断言最后一次索引文件可正常 `json.loads`。
    - 模拟 5 个并发 `await rewrite_chapter(...)` 同一章，断言所有 label 唯一、无覆盖（通过读 `ch_NNNN_vX.txt` 的内容哈希）。
- **风险**：
  - 进程内锁仅限本进程；多 worker 部署下同章并发仍可能冲突（README 已说明 `workers > 1` 不支持 MasterKey 缓存）。
  - 锁 dict 不会自动清理 key（项目生命周期 ≈ 后端进程，内存可控；后续若引入大量 short-lived project_id 可加 LRU，超出本次范围）。

### 2.4 前端 client.ts 5xx 错误体暴露

- **文件**：`frontend/src/api/client.ts:96-99`
- **现状**：`throw new Error(\`请求失败 ${resp.status}: ${path} ${text}\`)` 把后端原始响应体（可能含 SQL/堆栈）直接给前端 toast。
- **修复**：
  1. 拆分 4xx 与 5xx：
     - 5xx（status >= 500 且 status < 600）：只抛 `"服务器错误 ${resp.status} (${path})"`，不读 body。
     - 4xx：保留原行为（4xx 通常是业务错误，detail 是用户可读信息）。
  2. JSON 解析失败路径（line 107-112）的 body[:200] 仍保留（属于解析层诊断，不涉及安全；只在解析失败时出现，不在主路径）。
- **测试**：
  - 新增 `frontend/src/api/__tests__/client.test.ts`（如已有 vitest setup；无则加 `vitest.config.ts` + `npm i -D vitest` 不在本次范围，则改为 Python 端 `requests`-style e2e 测试）：
    - mock fetch 返回 500 + body `"<html>StackTrace</html>"`，断言 Error message 不含 `<html>` 或 `StackTrace`。
    - mock fetch 返回 400 + body `'{"detail":"重复章节号"}'`，断言 Error message 含 `400` 与 `重复章节号`。
- **风险**：
  - 用户看不到后端 5xx 详情。如果生产环境有运维巡检，由 `GET /bridge/runs/{run_id}` + 日志系统承担；用户侧只看到"服务器错误"已足够。

### 2.5 `/health` detail 信息泄露

- **文件**：`backend/app/main.py:309-315`
- **现状**：`/health` 未鉴权即可访问；503 时返回 `str(e)[:200]` 详情（可能含 SQL/路径）。
- **修复**：
  1. NOVEL_PRODUCTION=1 模式：503 时只回 `{"status": "degraded", "db": "error"}`，去掉 `detail` 字段。完整错误仍走 `log.warning(...)`。
  2. dev 模式：保留 `detail` 字段（本地调试仍可见），但限制为 80 字符（从 200 收紧）。
- **测试**：
  - 新增 `backend/tests/api/test_health_redaction.py`：
    - monkeypatch `db.execute` 抛错 + 强制 `os.environ["NOVEL_PRODUCTION"]="1"`，断言 response body 不含 `detail` 键。
    - dev 模式：断言 `detail` 存在且 `len(detail) <= 80`。
- **风险**：
  - 极低。健康检查是诊断端点，dev 模式可见详情对本地调试友好。

### 2.6 Dashboard chapter-fan 点击冒泡

- **文件**：`frontend/src/pages/Dashboard.tsx:476-493`
- **现状**：`chapter-fan` div 无 `pointer-events: none` 也无 `onClick={(e) => e.stopPropagation()}`，点击会冒泡触发父级 project card 导航。
- **修复**：
  - `chapter-fan` 容器加 `style={{ pointerEvents: "none" }}`。该层只是视觉装饰（aria-hidden 已有），不响应交互最安全。
- **测试**：
  - 不写新测试，依赖视觉手动验证（用户已确认此问题）。
  - 在 PR 描述里附 before/after 截图（如用户要求）。
- **风险**：
  - 极低。仅影响点击是否触发父级导航，不影响其他交互。

### 2.7 伏笔 `linked_character_id` 静默丢失

- **文件**：`backend/app/novel_extract.py:488-498`
- **现状**：`name_to_id.get(linked_name)` 返回 None 时，伏笔仍以 `linked_character_id=None` 入库，且不加入 warnings。
- **修复**：
  1. 当 `linked_name` 非空但 `name_to_id.get(linked_name) is None` 时，追加 warning：`f"伏笔关联角色名 '{linked_name}' 未匹配到已入库角色，伏笔 '{fs.get('content')[:30]}...' 将以无关联角色入库"`。
  2. warnings 在 `extract_setting_from_chapters` 返回值中已透出到 API 调用方，本次仅补"关联失败"的告警来源。
- **测试**：
  - 现有 `backend/tests/api/test_novel_extract.py` 增补：
    - 构造 LLM 返回 `foreshadowings: [{content: "X", linked_character_name: "不存在的角色"}]`，断言返回的 warnings 含 "未匹配到已入库角色"。
- **风险**：
  - 极低。仅多一条用户可见警告，行为不变。

### 2.8 `extract_setting_from_chapters` 持久化异常缺日志

- **文件**：`backend/app/novel_extract.py:668-671`
- **现状**：`except Exception: db.rollback(); raise` 无 `log.exception`，运维无法定位失败阶段。
- **修复**：
  - `except Exception:` 中先 `log.exception("extract_setting persist failed for project_id=%s", project_id)`，再 `db.rollback(); raise`。
- **测试**：
  - 单元测试：用 monkeypatch 让 `_persist_world` 抛错，断言 caplog 记录到 "extract_setting persist failed"。
- **风险**：
  - 极低。日志层补充，行为不变。

### 2.9 Provider/RoleAssignment 显式 dev/prod 注释 + dev 模式保留

- **文件**：`backend/app/api/providers.py:39-90`、`backend/app/api/role_assignments.py:20-73`、`backend/app/models.py:255-283`
- **现状**：
  - 两张表无 owner 字段（Phase 1 全局共享）。
  - dev 模式无 token 也能 list/create/update/delete provider（CLAUDE.md 允许本地原型）。
  - prod 模式仍然无 401/403（端点路径不挂 owner check）。
- **修复（最小）**：
  1. **不增加 owner 字段**，不增加表/列/API。
  2. `providers.py` 与 `role_assignments.py` 在每个端点 docstring 加显式说明：「Phase 1 全局共享配置，dev 模式匿名允许，prod 模式下仍由 NOVEL_PRODUCTION 全局 fail-fast 校验与 middleware 兜底；后续如需 per-user 隔离应新增 `provider_owners` 关联表（不在本次范围）」。
  3. 不在端点层加 `Depends(get_current_user_optional)`，避免引入与现有 `auth_scope.py:owner_filter_clause` 模式不一致的过滤；统一由 `NOVEL_PRODUCTION=1` 启动时的 fail-fast 检查承担"线上不允许单租户"职责。
- **测试**：
  - 不写新测试（仅注释改动）。
- **风险**：
  - 仍存在"prod 模式下 A 用户可读写 B 用户的 Provider 配置" 风险。但 README 与 CLAUDE.md 已声明「当前是原型阶段 / 多用户隔离是后续工作」；本次不引入新表，行为不变。

### 2.10 Cookie vs localStorage 注释与现状对齐

- **文件**：`backend/app/auth.py:213-217`、`frontend/src/api/client.ts:79-82`、`backend/app/api/auth.py:50-72`（cookie 签发处）
- **现状**：
  - 后端 `_set_auth_cookie` 签发 HttpOnly cookie，但 `_extract_bearer` 仅解 Authorization header。
  - 实际生效路径：前端 `getStoredToken()` 读 localStorage，client.ts:79-82 拼到 Authorization 头。
- **修复（最小）**：
  1. `backend/app/auth.py:213-217`：在 `_extract_bearer` 上方加 docstring 说明"当前 cookie 暂未被读取（参见 frontend/src/api/client.ts 走 localStorage）；cookie 是为未来 'cookie-only 路径' 预留"。
  2. `backend/app/api/auth.py:50-72`：`_set_auth_cookie` docstring 末尾加一行「注：cookie 当前未在请求路径中被读取，仅作未来切换预留」。
  3. `frontend/src/api/client.ts:79-82`：注释加「token 存 localStorage（见 backend/app/auth.py:_extract_bearer）；cookie-only 切换不在本轮范围」。
  4. **不切换到 cookie-only**（涉及 CSRF 防护 + 反代 + 多 worker 同步，超出最小实现）。
- **测试**：
  - 不写新测试。
- **风险**：
  - 现状与注释一致即可；token 仍在 localStorage（CLAUDE.md 接受 XSS 前提："当前前端无 dangerouslySetInnerHTML"）。

### 2.11 `_terminate_process_tree` 冗余别名清理

- **文件**：`backend/app/api/bridge.py:65`
- **现状**：`grep _terminate_process_tree` 仅在定义处命中（一处），无其他调用。
- **修复**：
  - 删除该行；所有调用点改用 `_kill_process_tree(pid, force=False)`（无调用方，无须改）。
  - 在 commit 信息中记录"审计 #11：清理冗余 lambda 别名"。
- **测试**：
  - 行为不变；现有 watchdog 测试覆盖。
- **风险**：
  - 极低。

### 2.12 `BridgeRun.stdout_text` 增长上限

- **文件**：`backend/app/api/bridge.py:355/361/368`
- **现状**：每 50 行 flush 时直接 `+ "".join(stdout_chunks)`，长任务内存 + DB 体积持续增长。
- **修复**：
  1. 引入常量 `_STDOUT_TEXT_MAX = 1_000_000`（约 1 MB），文件顶部集中定义。
  2. 每次 append 前检查长度；超过上限时，丢弃头部并保留尾部（保留「最近 ~1 MB」），同时记录 `bridge_run.stdout_truncated = True`（新列；如不想加列则在 metadata json 表达）。
  3. **简化方案**：不增加列；用「环形截断」—— 超过上限后 `bridge_run.stdout_text = bridge_run.stdout_text[-_STDOUT_TEXT_MAX:]`，并 `log.info("bridge_run stdout_text truncated for run_id=%s len=%d", run_id, old_len)`。DB 写入仍是完整 1MB，不再增长。
  4. 前端 `BridgeConsole` 已经按事件流逐行展示，截断只影响"事后查 history"。
- **测试**：
  - 新增 `backend/tests/api/test_bridge_stdout_truncation.py`：
    - monkeypatch stdout 持续输入超过 1 MB 的数据，断言 `bridge_run.stdout_text` 长度 ≤ 1 MB。
- **风险**：
  - 截断策略「保留尾部」对长运行调试不友好（看不到开头）。但 SSE 实时流已能展示全部事件，stdout_text 是 SSE 断开后的 fallback 用途，保留尾部合理。
  - DB TEXT 字段（SQLite）实际无长度限制，但内存里 Python str 长度与写入放大是问题源头。

---

## 3. 共同边界

### 3.1 文档更新

- `docs/wiki/03-Writing-Engine.md`：在「落盘/恢复」一节加一行"索引文件（rewrite_candidates.json / bootstrap_candidates.json）走 atomic_write_json"。
- `docs/wiki/01-Architecture.md`：在「后端-引擎同步」一节加"stdout_text 保留尾部 ~1MB，环形截断"。
- `docs/wiki/04-Frontend.md`：在「API 客户端」一节加"4xx 错误体可透传，5xx 错误体不暴露"。
- `docs/wiki/02-Backend-API.md`：在 `/health` 段加"prod 模式不返回 detail"。

### 3.2 测试

- 行为测试：12 项修复各加至少 1 个新测试。
- 不变量测试：`backend/tests/invariants/` 现有项无需新增（修复不涉及表结构 / Schema 变化）。
- 单独 pytest 进程运行（CLAUDE.md 要求）：
  - `pytest backend/tests --ignore=backend/tests/invariants`
  - `pytest backend/tests/invariants`

### 3.3 不变项

- 不新增依赖、环境变量、数据库表、公共 API。
- 不修改核心 Agent prompt 或 LangGraph 拓扑。
- 不复制许可证不兼容代码。
- 行为正确性优先于性能；性能改进不得破坏现有契约。
- 一次性最小化提交；不创建 phase/iteration/audit 报告。

---

## 4. 风险与未做项

| 项 | 状态 | 备注 |
|----|------|------|
| Provider/RoleAssignment per-user 隔离 | 暂不做 | 需新表，CLAUDE.md 禁止未经授权增加数据库表 |
| Cookie-only 鉴权切换 | 暂不做 | 涉及 CSRF/反代/多 worker，超出最小实现 |
| Dashboard loadAll 静默吞错 / Promise.all 并发限制 | 暂不做 | 低优先级（审计 #2/#3） |
| migrations.py f-string 拼接 DDL 风格 | 暂不做 | 审计已记录为"非本次引入" |
| ChapterReader 四个 useEffect 合并 | 暂不做 | 仅代码风格，不影响功能 |

---

## 5. 验证清单（交付前必跑）

```bash
# 行为测试
pytest backend/tests --ignore=backend/tests/invariants -q

# 不变量测试
pytest backend/tests/invariants -q

# Python 编译
python -m compileall -q backend/app backend/engine backend/scripts backend/tests

# 前端构建
npm --prefix frontend run build

# 预检
git diff --check
git status --short
```

必须如实报告实际运行结果；未运行或失败的检查不声称通过。
