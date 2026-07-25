# 写作引擎（`backend/engine/`）

LangGraph 状态机驱动的多 Agent 网文写作引擎，被后端以子进程方式调用（见 [01-Architecture.md](01-Architecture.md)）。早期作为独立版 `novel_AI/` 仓库维护，2026-07 起并入 `backend/engine/`；迁移与加固历史见本文末尾「[移植自独立版 novel_AI/ 的关键加固](#移植自独立版-novel_ai的关键加固)」表格。

## LangGraph 状态机

图构建于 `backend/engine/orchestrator.py` 的 `build_graph()`（:679），并在 `backend/engine/graph.py` 的 `build_project_graph()`（:262）中包一层 SSE 事件包装。**6 个图节点**（9 个 Agent 是被节点调用的函数，不是独立的图节点）：

| 节点 | 函数 | 内部调用 |
|------|------|----------|
| `load_arc_tasks` | `node_load_arc_tasks`（:184） | `run_outline` / `run_outline_card` / `run_outline_talk`（三种大纲模式，由 `NOVEL_OUTLINE_MODE` 决定）；预算硬停检查 |
| `get_next_task` | `node_get_next_task`（:309） | 从 `chapter_task_queue` 弹任务，重置 `rewrite_count_current` |
| `write_pipeline` | `node_write_pipeline`（:323） | `run_writer → run_normalizer →`（草稿/个人平台模式跳过）`run_compliance → run_checker` |
| `rewrite` | `node_rewrite`（:440） | `run_rewriter`（P0/P1/P2 级）`→ run_normalizer → run_compliance → run_checker(mode="lite")` |
| `save_and_track` | `node_save_and_track`（:530） | 落盘章节正文+元数据、`run_tracker`，弧结束时额外跑 `run_summarizer` |
| `human_escalation` | `node_human_escalation`（:595） | 追加 `human_pending` 任务，保存 `[待修订]` 标记章节 |

### 路由逻辑

- `route_after_pipeline`（:627）：写作/合规/质检抛异常 → `escalate`；`final_score ≥ PASS_SCORE(6.5)` → `save`；否则 `rewrite_count < MAX_REWRITE(3)` → `rewrite`，达到上限 → `escalate`
- `route_after_rewrite`（:648）：同上逻辑，重写后再次判定
- `route_after_save`（:667）：还有任务/弧 → 回到 `get_next_task`（循环 `load_arc_tasks`）；否则 → `done`
- `human_escalation → load_arc_tasks`：循环回到下一章，而非终止（修复了独立版 `novel_AI` 中曾经的 `→ END` 提前终止 bug）

重写等级由 Checker 的 `final_score` 决定：`≥7.5` PASS，`≥6.5` PASS_WITH_NOTE，`≥5.5` REWRITE_LIGHT(**P2**)，`≥4.5` REWRITE_MEDIUM(**P1**)，`<4.5` REWRITE_HEAVY(**P0**)；合规检查失败强制 **P1**。预算阈值 `BUDGET_WARN=1.00`、`BUDGET_HARD=1.50`（150%，刻意放宽）。

**入口函数**：`run_graph_task(project_id, command, args, run_id, queue)`（`graph.py:343`），按命令名分发约 20 种操作：`run`/`resume`/`run_draft`/`planner`/`bootstrap`/`init_arc`/`test`/`budget`/`scan`/`fingerprint`/`export`/`stats`/`show`/`human_review`/`style`/`calibrate`/`acceptance`/`status`/`pending`/`set_audit_mode`。

## 9 个 Agent

全部位于 `backend/engine/agents/`，通过 `llm_router.py:get_active_router()` 获取共享的 `LLMRouter` 实例。

| Agent | 文件:关键函数 | 读取 | 写出 | 默认 LLM |
|-------|--------------|------|------|----------|
| Planner | `planner.py:150 run_planner` | `novel_config.json` | `setting_package.json`（Schema 校验） | Claude Sonnet |
| Outline | `outline.py:34 run_outline`（+`run_outline_card`/`run_outline_talk`） | 弧规划、设定、L2 热层 | `ChapterTask[]` | DeepSeek |
| Writer | `writer.py:188 run_writer` | 任务、L2 写作上下文（`get_writer_context`）、设定 | 章节草稿 | Claude Sonnet，用 `call_with_length_budget` |
| Normalizer | `normalizer.py:88 run_normalizer` | 原始文本 | 去 AI 腔文本 + 格式问题 | 触发时才走 LLM 二次通道 |
| Compliance | `compliance.py:123 run_compliance` | 文本、平台 | passed/hard_rejects/warnings | DeepSeek（正则 + LLM 两级） |
| Checker | `checker.py:99 run_checker` | 文本、任务、audit_mode、规则预检反馈 | score/verdict/rewrite_level/六维分 | main=DeepSeek, cross1=Claude, cross2=DeepSeek，加权 0.5/0.25/0.25 |
| Rewriter | `rewriter.py:199 run_rewriter` | 草稿、级别、反馈、质检结果、记忆 | 重写文本 | Claude Sonnet |
| Tracker | `tracker.py:103 run_tracker` | 章节文本、任务、当前 L2 | 更新后的 L2（热/冷/约束/元） | DeepSeek |
| Summarizer | `summarizer.py:123 run_summarizer` | 触发条件、弧、L2 | L5 弧摘要/压缩历史 | Claude Sonnet |

另有 `init_arc.py:13 build_state_from_setting`（仅 bootstrap 阶段用，无 LLM，纯数据转换：`setting_package.json → orchestrator_state.json.arc_plans`）。

## 记忆系统（`backend/engine/memory/manager.py`）

三层结构：

- **L2 热层**：`protagonist_level/points`、`inventory`、`character_states`、`active_threads`、`recent_summaries`、`scene_location`、`time_context`（近约 20 章）
- **L2 冷层**：`compressed_history`、`closed_threads`、`resolved_foreshadowing`、`world_events`
- **L2 约束**：`forbidden_constraints`（自动过期）、`established_facts`、`foreshadowing_planted`
- **L5 弧级归档**：`arc_summaries`、`character_arcs`、`major_revelations`、`compressed_history`，弧结束时由 Summarizer 写入

**按需检索**：`get_writer_context()`（:445）→ `get_l2` → `expire_constraints` → `get_chapter_relevant_context()`（:272，按任务 `main_characters` 过滤 + 最近 5 条摘要 + ≤5 条相关约束 + ≤3 条到期伏笔）→ 附加风格样本。Writer 每章只注入约 1500 token 的按需上下文，而非完整记忆。

**热→冷压缩**：`recent_summaries` 超过 `HOT_TO_COLD_THRESHOLD(20)` 触发 `maybe_compress_hot_to_cold`（:138），最老 10 条压入冷层；若压缩候选超过 `SECONDARY_SUMMARIZE_SOFT_CAP(4000 字符)`，用 LLM 二次摘要到约 1500 字符（`_secondary_summarize_cold_history`，:224），而非硬截断（修复过数据丢失 bug）。

**风格样本**：内部样本（前 3 高分章节，20 章后启用）vs 外部样本（`style_samples/*.txt`），每 30 章刷新一次（`maybe_update_style_samples`，:416）。

**容错**：所有 JSON 读取失败时会先备份 `.corrupted.{ts}` 文件再回退默认值，而非静默丢数据（`_load_json_or_default`，:329）。

## 从后端调用引擎

`POST /bridge/run` → 创建 `BridgeRun` 行 → `BackgroundTasks.add_task(_spawn_engine_subprocess, ...)`（`app/api/bridge.py:186`）→ `subprocess.Popen([sys.executable, engine/workers/run_bridge_subprocess.py, run_id, project_id, command, *args, outline_mode], stdout=PIPE, stderr=STDOUT, cwd=BACKEND_ROOT)`。

`run_bridge_subprocess.py:main()` 将 `backend/` 加入 `sys.path`，设置 `NOVEL_OUTLINE_MODE`，调用 `engine.graph.run_graph_task(project_id, command, args, run_id, queue=None)`（`queue=None` 时引擎的 `SSECapture` 直接写向 `sys.__stdout__`，而非进程内 Queue）。

后端一个后台线程逐行读取子进程 stdout，每行包成 `{"event": "log", "line": ...}` 推入 SSE 队列，每 50 行 flush 一次到 `BridgeRun.stdout_text`，进程退出时置 `status="done"/"failed"` 并推送 `complete`/`done` 事件。`GET /bridge/stream` 通过 `EventSourceResponse` 把该队列转发给前端。

## 预算与成本追踪（`tools/budget_manager.py`）

每次 `router.call()` 记录成本到内存 `_stats`（`llm/router.py:_record`），编排节点累加进 `state["budget_used_usd"]`。`budget_manager.log_cost()` 追加 JSONL 记录到 `output/logs/budget_log.jsonl`（章节/Agent/模型/token/成本）。`generate_report()`（:64）综合 `orchestrator_state.json` + JSONL 日志，计算总成本、按 Agent/弧分组、近 20 章章均成本、按此外推的项目总成本，在 80%/95% 或预计超支时告警；`print_report()` 原子写出 `reports/budget_report.json`。硬停：`BUDGET_HARD=1.50` 触发时暂停并生成 `budget_exceeded` 人工待办，而非直接中止。

## 合规与质量门（三道防线）

1. **Compliance**（`compliance.py`）：两级——先免费正则 `keyword_scan()` 匹配 `config/compliance_rules/compliance_rules_fanqie.json`（5 条硬性拒绝规则：政治人物、血腥暴力、敏感宗教/分裂组织、未成年性内容、真实地点+犯罪组合；1 条警告规则；字数限制），命中硬性关键词时跳过 LLM 语义检查省成本；LLM 返回解析失败按 FAIL 处理（fail-closed，修复过"假通过"bug）。
2. **Rule Checker**（`rule_checker.py`）：每章质检前执行的零成本规则层，检查占位标记、陈词滥调、重复开场、过长段落和可疑正文包装；结果写入运行状态并作为先验反馈传给 Checker。
3. **Checker**（`checker.py`）：6 维度加权评分——节奏 25%、人物声音 20%、剧情逻辑 15%、设定一致性 15%、文笔自然度 15%、钩子力度 10%。`full` 模式下三模型交叉（主评 50% + 两次交叉各 25%），`lite`/`bootstrap` 模式单模型；旧 `shuang_density` 结果仍可兼容读取。
4. **Fingerprint**（`fingerprint_checker.py`）：纯统计（无 LLM）AI 写作指纹检测——句长标准差、段首字符重复率、AI 对话引导词（说道/笑道等）计数、感叹号/省略号密度、AI 词汇黑名单，0-100 分，≥60 高风险；另检测角色口癖是否落实（`check_character_voices`）。

跨章一致性由 `chapter_checker.py` 负责：局部正则检查（点数逻辑、境界非法跳级）+ 每 10 章一次的 LLM 一致性核查（对照已知 L2 事实）。`acceptance_tests.py` 提供 5 项 AC 验收标准（设定一致性、题材切换覆盖、任务单质量、平台字数/钩子合规、角色弧一致性），是独立 CLI 测试套件，不在每章流水线内。

## 工具集一览（`backend/engine/tools/`）

| 文件 | 职责 |
|------|------|
| `bootstrap.py` | 黄金三章多版本（A/B/C）生成，供人工选定风格锚点 |
| `human_review.py` | 交互式人工审核（accept/reject/edit） |
| `exporter.py` | 章节汇编导出为平台格式 TXT |
| `budget_manager.py` | 成本记录、预算预警、投影分析 |
| `chapter_checker.py` | 跨章节一致性扫描 |
| `rule_checker.py` | 每章零成本规则预检，并向 LLM Checker 提供明确问题摘要 |
| `fingerprint_checker.py` | 文风指纹统计检测 + 角色口癖检测 |
| `style_manager.py` | 风格样本库管理 |
| `calibrate_checker.py` | Checker 基线校准 |
| `acceptance_tests.py` | 五大验收标准（AC-1~5） |
| `system_test.py` | 集成测试套件（含 Mock LLM） |

## `backend/engine` 移植自独立版 `novel_AI/` 的关键加固

修订 2026-07-16：`novel_AI/` 独立版仓库已删除（gitignored 历史参考实现），所有逻辑迁移到 `backend/engine/`。下表是迁移时同步引入的关键加固：

| 维度 | 独立版 `novel_AI/` | `backend/engine` | 加固目的 |
|------|--------------------|------------------|----------|
| **LLM 路由状态** | `api_client.py` 用模块级全局变量 | `LLMRouter` 改为按实例，支持多项目/多线程互不干扰 | 后端多租户并发隔离 |
| **API Key 来源** | 纯环境变量（`.env` 文件） | `engine/llm_router.py` 从 `Provider` / `RoleAssignment` 表读加密 API Key + 代理配置，注入 router | Web 配置化，运行时切换 Provider |
| **进程隔离** | `run.py` 普通 CLI 入口 | `workers/run_bridge_subprocess.py` 子进程入口 | uvicorn 重启/热重载不杀 in-flight run |
| **Mock Provider** | 无 | `llm/router.py:_mock()` 含完整按 Agent 分类的 mock 响应 | CI/测试免费跑全流程 |
| **JSON 写入** | 部分文件用 raw `open(w)` | 全部走 `atomic_write_json` | 半写损坏防护（ch_0064 等假通过 bug 根因之一） |
| **故障行为** | fail-silent（用 `except: pass` 兜底） | fail-loud（`log.exception` + 标记 `meta._xxx_failed`） | 让运维能定位真实失败原因 |
| **路由死路** | `human_escalation → END`（运行提前终止） | `human_escalation → load_arc_tasks`（循环回下一章） | 300 章实测暴露的真实 bug |
| **记忆合并** | 无去重感知 | `memory/manager.py` 去重感知合并 | 防止 L5 摘要重复吃预算 |
| **成本追踪** | 各处 `cost += x` 散落 | `_record()` 统一入口，hash-by-token 防重复计费 | 300 章实测 `$15`，重复扣费曾导致硬停阈值失效 |
| **Checker 校准** | hardcoded 阈值 | `calibrate_checker.py` + `calibration/` 标注样本 | 阈值可调，不靠拍脑袋 |

`dashboard` 命令尚未移植（`graph.py:456` 显式标注为 P3 待办——Web BridgeConsole 已取代大部分功能）；`card`/`talk` 大纲模式和 `run_draft`/`set_audit_mode` 命令是 backend 独有的新增能力，独立版 `run.py` 中没有。

> 历史溯源：迁移时每个文件头部注释都标注 "Migrated from novel_AI/..."。可 `git log --follow <file>` 追溯到迁移 commit（最早是 phaseD 的 `2e80fec`，后续 phase9 / phaseA / phaseB / phaseC 持续加固）。

---

## 已知方法论 gap 与补全计划（2026-07-25）

> 本节为 **方法论密度补全 TODO**，与"已交付能力"分开。来源是真实 LLM 30 章测试后做的战略审视（P0 修正 + Gap 改写），仅记录**当前未实现但已识别**的能力点。每条都标注了**前置条件（不违反 CLAUDE.md 硬约束）**与**最小必要验证**。

### 0. 工程现状速校（修订报告中的 5 项事实更正）

| # | 原报告 | 工程现状 | 备注 |
|---|--------|---------|------|
| F-1 | "Claude Opus 正文 / Kimi 校验" | `router.py:39-49` 默认是 **Claude Sonnet 4.5 主笔 + DeepSeek 校验**；Opus/Kimi 在 router 中可用但未启用 | 来源 [engine/llm/router.py:37-50](../engine/llm/router.py) |
| F-2 | "$200-300/百万字" | 实测 $0.74 / 64,545 字 = **$11.46/百万字** | 来源 [CHANGELOG.md](../../CHANGELOG.md) + [07-Real-LLM-Testing.md](07-Real-LLM-Testing.md) |
| F-3 | "12 项自动验收" | `acceptance_tests.py` 实际只有 **AC-1~AC-5**；12 项是规划目标，不是现状 | 后续补全时按"追加 AC-6~AC-12"计算工作量 |
| F-4 | "小爽点 1 章周期" | 自相矛盾（同一文档 3 个数字）| 统一为"小爽点 3 章 1 个 + 大爽点 10 章 1 个" |
| F-5 | "五层记忆 L1/L2/L3/L4/L5" | 项目自称三层（L2 热/冷/约束 + L5 弧级归档），L1/L3/L4 不存在；L2 是 JSON 非向量 | 来源 [03-Writing-Engine.md §记忆系统](03-Writing-Engine.md) |

### 1. 方法论密度 gap 清单（已识别但未落地）

> 标注:**风险等级** + **是否违反 CLAUDE.md 硬约束** + **最小验证方式**

#### Gap-M1 🟧 但是法则 / 筹码 / 两难结构化（obstacles+stakes+dilemma）

- **现状**: `chapter_task.chapter_goal` 是自由文本，无结构化阻碍/筹码/两难字段
- **风险**: 无（schema 扩展，不改表）
- **CLAUDE.md 兼容性**: ✅ 兼容（不增加数据库表，不修改核心 Agent prompt——只在 `shared/setting_models.py` 加可选 Pydantic 字段，`extra='allow'` 已支持）
- **最小验证**: outline agent 输出新字段 → writer prompt 读取并渲染 → 跑 3 章真 LLM，对比改前改后文本

#### Gap-M2 🟧 信息差 / 三线并行 / 锚点归一字段

- **现状**: 缺 `info_asymmetry` / `narrative_thread` / `anchor_to` 字段
- **风险**: **LLM 可能瞎填**——需要 few-shot 演示，否则字段噪声大
- **CLAUDE.md 兼容性**: ✅ 兼容（仅扩展 Pydantic schema，不改表）
- **最小验证**: 人工写 3 个标准示例，让 outline agent 模仿；输出与人工示例的字段填充一致性 ≥ 70%

#### Gap-M3 🟧 情绪锚点（emotion_core / emotion_intensity）

- **现状**: 完全缺失。3 篇 .docx 长文（《告别单机》§8 / 《告别"自嗨"》§1 / 《叙事架构执行手册》§3）显式要求每章必填情绪核点
- **风险**: 无
- **CLAUDE.md 兼容性**: ✅ 兼容（字段扩展）
- **最小验证**: 写 30 章回顾，对每章标情绪核点，与现有 `quality_history` 对照一致性 ≥ 60%

#### Gap-M4 🟧 扮猪吃虎 / 打脸三阶段节拍校验

- **现状**: 缺强制三阶段校验器
- **风险**: 无（新增 `engine/tools/beat_checker.py`，纯离线 CLI）
- **CLAUDE.md 兼容性**: ✅ 兼容（仅新增 engine 内部工具，不暴露公共 API）
- **最小验证**: 跑 31 章真实产出，校验"最近 10 章必含 ≥1 次完整三阶段"

#### Gap-M5 🟨 对话癌后处理阈值

- **现状**: `normalizer.py` 主要剥 AI 腔，没处理对话提示词污染
- **CLAUDE.md 兼容性**: ✅ 兼容
- **原报告阈值**: "每章 ≥5 次触发" — **错误**
- **正确阈值**: 每章"说/道/问道/回答" ≥ **25-30 次**触发预警，≥50 强制替换（同 200 字段内连续 ≥3 次同类提示词也触发）
- **最小验证**: 抽样 3 章 + 跑 normalizer，输出 diff

#### Gap-M6 🟨 视角锁定（POV 切换密度）

- **现状**: writer prompt 无 POV 锁定约束
- **CLAUDE.md 兼容性**: ✅ 兼容
- **最小验证**: 加 prompt 约束后跑 3 章，统计 POV 切换次数应 ≤ 2/章

### 2. **CLAUDE.md 硬约束红线 — 已在审视中识别为"看似合理但实际违规"的建议**

> 这些改造方向**不能**按原报告执行，必须先取得维护者授权。

| 原报告建议 | CLAUDE.md 红线 | 当前状态 |
|----------|----------------|---------|
| 新增 `EntityStateTimeline` 表 | "**未经任务明确授权，不增加数据库表**" | ❌ 不执行 |
| 新增 `plot_engineer` 独立 LangGraph 节点 | "**不修改核心 Agent prompt 或 LangGraph 拓扑**" | ❌ 不执行 |
| Neo4j Python embedded 嵌入 | "**不增加依赖**" | ❌ 不执行 |
| 番茄签约 API 自动报送 | "**不增加公共 API**" | ❌ 不执行 |
| 反洗稿自检服务化 | "**不增加公共 API**" | ❌ 不执行（可作为离线 CLI） |

**改写后的合规替代方案**:
- EntityStateTimeline → 复用现有 `EntityRelation` 表 + `EntityRelationRich` 模型（`shared/setting_models.py:163-169`）+ 扩展 `EmbeddingChunk.source_type` 加 `entity_state` 类别（已有 source_type 字段，无需迁移）
- plot_engineer → 在 `rewriter.py` 的 prompt 中加反转套路分支（单 agent 多 prompt，不动拓扑）
- Neo4j → 不引入；用现有 `L2 cold.world_events` JSON 列表模拟时间线
- 番茄签约 → 生成"报送材料草稿"（让用户复制粘贴），不做 API
- 反洗稿 → 仅作为离线 CLI 工具，参考作品由用户上传，不做服务化

### 3. 风险与护栏（在 LLM 实施前必须就位）

> 这些是 30+ 章真实 LLM 测试才会暴露的"工程层风险"，原报告完全没提。

| # | 风险 | 护栏 |
|---|------|------|
| R-Prompt | writer prompt 加 4 招方法论后 token 涨 30-50% | `_call_with_budget` 已有长度控制；输入侧需加硬上限 ≤ 6000 token + 超额字段降级 |
| R-MasterKey | 新 schema strict 校验拦掉老数据 | `ConfigDict(extra='allow')` 已配置；新字段必须 `default=None` + `dict.get` 兜底 |
| R-Checkpoint | 子进程 SIGTERM 后 SqliteSaver 连接句柄泄漏 | `close_all_checkpointers()` 已在 `graph.py:118` 实现；需补"跨进程 resume 时 checkpoint 兼容性" e2e 测试 |
| R-TokenPlan | 100 章跑通会撞 Token Plan 速率限制（CLAUDE.md §真实 LLM 经验） | 第三阶段验收必须配套 Token Plan 升级到能跑 100 章的档位 |
| R-MultiUser | prod 模式新工具未带 `owner_id` 过滤 | 所有新工具强制 `require_owned_project` 校验 |

### 4. Commit 0: writer prompt 方法论内化（最小必要改进）

> 这是**0 架构变更** + **风险最低** + **回报最高**的最小必要改进。**不需要新表、不需要新 agent、不需要新 prompt 字段**——只深化已有 `prompt_templates.py` 的执行指令。

#### 4.1 已有提示深化（不动 Schema）

- **7 钩子** (`HOOK_TYPES`)：从"desc + pattern + example"扩为"**断章几何位置三原则**"（高潮前 0.5s / 答案前 1s / 信息差将闭时）
- **7 爽点** (`SHUANG_TYPES`)：每类加"节拍位提示"（"本爽点适合章内 30%/60%/90% 哪个位置"）
- **3 题材** (`GENRE_WRITING_INSTRUCTIONS`)：补充"信息差 / 但是法则 / 3 层期待感 / 模块化叙事" 4 招操作清单

#### 4.2 4 招方法论模块（新增到 `prompt_templates.py`）

```python
# 4 招方法论：信息差 / 但是法则 / 3 层期待感 / 模块化叙事
WRITER_METHODOLOGY = {
    "info_asymmetry": "...",      # 信息差 3 模式（读者知/主角知/双方不知）
    "but_law": "...",             # 但是法则：每章必有 ≥1 个但是转折
    "three_layer_hook": "...",    # 微观/中观/宏观钩子
    "modular_narrative": "...",   # 主线/支线/暗线模块化
}
```

每个方法论给"5 行具体执行清单"（如 "但是法则 → 章首 200 字必有目标 → 章中 1/3 处必有阻碍 → 章中 2/3 处必有但是 → 章尾必有钩子"）。

#### 4.3 测试矩阵（不依赖 LLM，纯字符串测试）

- 7 钩子 × 3 题材 = 21 个 fixture 验证 `get_hook_guidance` 输出非空
- 7 爽点 × 3 题材 = 21 个 fixture 验证 `get_shuang_guidance` 输出非空
- 4 招方法论 × 3 题材 = 12 个 fixture 验证模块化输出

#### 4.4 工作量与回报

- 文件改动: 3 个（`prompt_templates.py` + `writer.py` + 新测试文件）
- 代码量: ~150 行（4 模块各 30 行）+ 测试 200 行
- 风险: 极低（不动 Schema，不动 Agent 拓扑，仅深化已有字段的 prompt）
- 回报: writer 生成文本立即按方法论执行，无需依赖 outline agent 新字段填对

### 5. 后续 commit 排序（仅作 backlog，不写独立报告）

| 优先级 | Commit | 解决的 Gap |
|--------|--------|----------|
| ⭐⭐⭐⭐⭐ | **Commit 0**: 4 招方法论写入 prompt_templates.py | M1/M2/M3/M5/M6 的 prompt 层落地 |
| ⭐⭐⭐⭐ | Commit 1: 但/但是法则 + stakes + dilemma 字段扩展 | M1 字段化 |
| ⭐⭐⭐⭐ | Commit 2: info_asymmetry + narrative_thread + anchor_to 字段 | M2 字段化 |
| ⭐⭐⭐⭐ | Commit 3: emotion_core + emotion_intensity 字段 | M3 字段化 |
| ⭐⭐⭐ | Commit 4: beat_checker 离线工具（扮猪吃虎 / 打脸三阶段校验） | M4 |
| ⭐⭐⭐ | Commit 5: 对话癌 normalizer 阈值修正 + 强制替换 | M5 |
| ⭐⭐⭐ | Commit 6: POV 视角锁定 prompt 约束 | M6 |
| ⭐⭐⭐ | Commit 7: 30 章自动验收脚本扩到 12 项 | R-Prompt + R-MasterKey 配套 |

每个 commit 走标准流程：改代码 → 加测试 → 跑两个 pytest 进程 → 跑 `npm run build` → 单 commit。

> **本节为 backlog**，不写独立 audit 报告（满足 CLAUDE.md 第 27 行）；实际执行时按上述顺序单个 commit 推进，每 commit 1 个聚焦改动。
