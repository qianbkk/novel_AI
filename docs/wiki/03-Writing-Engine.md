# 写作引擎（`backend/engine/`）

LangGraph 状态机驱动的多 Agent 网文写作引擎，被后端以子进程方式调用（见 [01-Architecture.md](01-Architecture.md)）。是从独立版 `novel_AI/`（参见 [07-Standalone-Engine.md](07-Standalone-Engine.md)）移植并加固的版本。

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
| Checker | `checker.py:99 run_checker` | 文本、任务、audit_mode | score/verdict/rewrite_level/五维分 | main=DeepSeek, cross1=Claude, cross2=DeepSeek，加权 0.5/0.25/0.25 |
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
2. **Checker**（`checker.py`）：5 维度加权评分——钩子力度 30%、爽感密度 25%、角色一致性 20%、剧情逻辑 15%、文笔自然度 10%。`full` 模式下三模型交叉（主评 50% + 两次交叉各 25%），`lite`/`bootstrap` 模式单模型。
3. **Fingerprint**（`fingerprint_checker.py`）：纯统计（无 LLM）AI 写作指纹检测——句长标准差、段首字符重复率、AI 对话引导词（说道/笑道等）计数、感叹号/省略号密度、AI 词汇黑名单，0-100 分，≥60 高风险；另检测角色口癖是否落实（`check_character_voices`）。

跨章一致性由 `chapter_checker.py` 负责：局部正则检查（点数逻辑、境界非法跳级）+ 每 10 章一次的 LLM 一致性核查（对照已知 L2 事实）。`acceptance_tests.py` 提供 5 项 AC 验收标准（设定一致性、题材切换覆盖、任务单质量、平台字数/钩子合规、角色弧一致性），是独立 CLI 测试套件，不在每章流水线内。

## 工具集一览（`backend/engine/tools/`）

| 文件 | 职责 |
|------|------|
| `bootstrap.py` | 黄金三章多版本（A/B/C）生成，供人工选定风格锚点 |
| `human_review.py` | 交互式人工审核（accept/reject/edit） |
| `exporter.py` | 章节汇编导出为平台格式 TXT |
| `budget_manager.py` | 成本记录、预算预警、投影分析 |
| `chapter_checker.py` | 跨章节一致性扫描 |
| `fingerprint_checker.py` | 文风指纹统计检测 + 角色口癖检测 |
| `style_manager.py` | 风格样本库管理 |
| `calibrate_checker.py` | Checker 基线校准 |
| `acceptance_tests.py` | 五大验收标准（AC-1~5） |
| `system_test.py` | 集成测试套件（含 Mock LLM） |

## `backend/engine` 相对独立版 `novel_AI/` 的关键加固

详见 [07-Standalone-Engine.md](07-Standalone-Engine.md#与-backendengine-的差异)。概括：去除模块级全局状态（改为按实例的 `LLMRouter`）、新增 DB 驱动配置层（`engine/llm_router.py` 从 `Provider`/`RoleAssignment` 表注入）、新增子进程隔离入口（`workers/run_bridge_subprocess.py`）、新增 Mock Provider（CI/测试免费跑）、大量以"迭代 #NN"注释标记的生产事故修复（原子 JSON 写入、fail-loud 替代假通过、去重感知的记忆合并、`human_escalation` 死路修复、成本重复计算修复）。`dashboard` 命令尚未移植（`graph.py:456` 显式标注为 P3 待办）。
