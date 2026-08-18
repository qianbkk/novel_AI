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
| Outline | `outline.py:34 run_outline`（+`run_outline_card`/`run_outline_talk`） | 弧规划、设定、L2 热层 | `ChapterTask[]`（含 stakes/dilemma/narrative_thread/info_asymmetry/anchor_to/emotion_core/emotion_intensity） | DeepSeek |
| Writer | `writer.py:188 run_writer` | 任务、L2 写作上下文（`get_writer_context`）、设定 | 章节草稿（4 招方法论 + POV 锁定 + 7 钩子 + 7 爽点 + 情绪锚点内化） | Claude Sonnet，用 `call_with_length_budget` |
| Normalizer | `normalizer.py:88 run_normalizer` | 原始文本 | 去 AI 腔 + 去对话提示词污染（≥50 触发强制 4 策略替换）+ POV 切换密度检测 | 触发时才走 LLM 二次通道 |
| Compliance | `compliance.py:123 run_compliance` | 文本、平台 | passed/hard_rejects/warnings | DeepSeek（正则 + LLM 两级） |
| Checker | `checker.py:99 run_checker` | 文本、任务、audit_mode、规则预检反馈 | score/verdict/rewrite_level/六维分 | main=DeepSeek, cross1=Claude, cross2=DeepSeek，加权 0.5/0.25/0.25 |
| Rewriter | `rewriter.py:199 run_rewriter` | 草稿、级别、反馈、质检结果、记忆 | 重写文本 | Claude Sonnet |
| Tracker | `tracker.py:103 run_tracker` | 章节文本、任务、当前 L2 | 更新后的 L2（热/冷/约束/元） | DeepSeek |
| Summarizer | `summarizer.py:123 run_summarizer` | 触发条件、弧、L2 | L5 弧摘要/压缩历史 | Claude Sonnet |

另有 `init_arc.py:13 build_state_from_setting`（仅 bootstrap 阶段用，无 LLM，纯数据转换：`setting_package.json → orchestrator_state.json.arc_plans`）。

## v1.0 Pre-Production 5 个 Agent

v1.0 起把"大纲质量"前置到正文之前——5 个新 Agent 在 10 阶段 worldbuild 之前先生成结构化产物，写每章时注入到 writer prompt。

| Agent | 文件 | 产物 | 默认 LLM |
|-------|------|------|----------|
| Genre Profiler | `genre_profiler.py` | 6 类男频（玄幻/仙侠/都市/历史/军事/科幻）的 reader_persona/tone_preference/taboo/show_item_examples/research_strength 三档分流 | DeepSeek |
| Theme Designer | `theme_designer.py` | theme_statement/expectation_arc (seed<twist<payoff)/resonance_anchors ≥3 共性维度 | DeepSeek |
| Opening Designer | `opening_designer.py` | 黄金三章（ch1_anchor/ch2_question/ch3_escalation），hook_type 严格 7 个合法之一，含 show_item_seed/used 接力 | Claude Sonnet |
| Research Notes | `research_notes.py` | 按 research_strength 三档分流 baseline（strong=5 维度，medium=system_consistency，weak=minimal） | DeepSeek |
| Macro Spine | `macro_spine.py` | 全书宏观弧（arc 边界连续、twist_chapter 必须落在某 arc 范围内），get_arc_for_chapter 写每章前调 | DeepSeek |

**Writer Prompt v2**：`_build_genre_block` / `_build_theme_block` / `_build_expectation_block` / `_build_showitem_block` 4 个 block 注入 writer prompt，把前期工程成果贯穿到正文。

**Scene Quality Check**（`scene_quality_check.py`）：4 维度（expectation_advanced / show_item_landed / resonance_hit / consistency_ok），任一失败 → escalate 给人工（**不自动 rewrite**，v1.0 决策），LLM 失败抛 SceneQualityCheckFailed（不让 silently PASS）。

## v1.0 3 个 Memory Ledger（`backend/engine/memory/`）

| 文件 | 作用 |
|------|------|
| `expectation_ledger.py` | 每章 expectation_status 落盘；`get_pending_seeds` 给下章追踪 |
| `show_item_chain.py` | 每章 show_item_used 接力；`get_recent_items` 给 writer prompt v2 用 |
| `voice_anchors.py` | 角色口癖记录 + `check_voice_consistency` 一致性检查 |

3 个 ledger 跟 L2/L5 是并行的"质量回环"——L2/L5 是按需检索的写作上下文，ledger 是按章节追踪的主题/物件/口癖一致性。

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

跨章一致性由 `chapter_checker.py` 负责：局部正则检查（点数逻辑、境界非法跳级）+ 每 10 章一次的 LLM 一致性核查（对照已知 L2 事实）。`acceptance_tests.py` 提供 **12 项 AC 验收标准**（AC-1~AC-12，详见下文「[方法论内化与节拍校验](#方法论内化与节拍校验)」），是独立 CLI 测试套件，不在每章流水线内。

`beat_checker.py` 是离线节拍校验器（扮猪吃虎 / 打脸三阶段 + 升级循环 + 情绪多样性 + 钩子存在性），扫 `output/chapters/ch_NNNN_meta.json` 产出红/黄/绿报告，AC-10/AC-11 复用其结果。

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
| `beat_checker.py` | 节拍校验器（扮猪吃虎 / 升级循环 / 情绪多样性 / 钩子存在），离线 CLI，红/黄/绿报告 |
| `acceptance_tests.py` | 12 项验收标准（AC-1~AC-12），离线 CLI |
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

## 方法论内化与节拍校验（2026-07-25~26 战略审视交付）

> 本节只描述当前可用能力。设计与审计过程保留在 Git 历史中，文档不依赖本地草稿或运行产物。

### 0. 关键事实速校(来自战略审视)

| # | 易混淆点 | 工程现状 |
|---|---------|---------|
| F-1 | 默认 LLM 路由 | `engine/llm/router.py:39-49` 是 **Claude Sonnet 4.5 主笔 + DeepSeek 校验**;Opus/Kimi 在 router 中可用但未启用 |
| F-2 | 单字成本 | 实测 $0.74 / 64,545 字 = **$11.46/百万字**(来源 [07-Real-LLM-Testing.md](07-Real-LLM-Testing.md)) |
| F-3 | 自动验收项数 | `acceptance_tests.py` 当前是 **AC-1~AC-12**(12 项,本轮新增 7 项) |
| F-4 | 爽点密度 | 小爽点每 3 章 1 个 + 大爽点每 10 章 1 个(节拍校验器以此为基线) |
| F-5 | 记忆层数 | L2 热/冷/约束 + L5 弧级归档(**L1/L3/L4 不存在**;L2 是 JSON 非向量) |

### 1. 已交付能力清单

#### 1.1 方法论 4 招内化(writer prompt)

`backend/engine/config/prompt_templates.py` 提供 4 个常量 + 1 个 `get_methodology_instruction(aspects)` 助手,在 writer prompt 中**默认全开**渲染(终章仅保留 3 层钩子):

| 招 | 常量 | 核心 |
|---|------|------|
| 信息差 | `INFO_ASYMMETRY_INSTRUCTION` | 读者知/主角不知 / 主角知/读者不知 / 双方均不知 三模式,每章 ≥1 |
| 但是法则 | `BUT_LAW_INSTRUCTION` | 章首 200 字 + 章中 1/3、2/3 + 章尾前 300 字 必有 ≥1 个转折信号 |
| 3 层期待感 | `THREE_LAYER_HOOK_INSTRUCTION` | 微观(本句) / 中观(本章) / 宏观(本弧) 三层钩子 |
| 模块化叙事 | `MODULAR_NARRATIVE_INSTRUCTION` | 主线/支线/暗线 + 锚点归一/高潮切断/漏斗汇聚 |

#### 1.2 ChapterTask 结构化字段(outline → writer → meta)

`backend/engine/state.py:ChapterTask` 通过 `NotRequired[...]` 增量加 7 个新字段,老 task JSON 自动兼容:

| 字段 | 类型 | 兜底 | 来源 |
|---|---|---|---|
| `stakes` | `{if_lose: [...], if_win: [...]}` | 无效 → `None` | 但是法则 Commit 1 |
| `dilemma` | `{option_a, option_b, both_cost}` | 无效 → `None` | 但是法则 Commit 1 |
| `narrative_thread` | `"main"`/`"side"`/`"hidden"` | 无效 → `"main"` | 三线 Commit 2 |
| `info_asymmetry` | `{reader_knows, protagonist_knows, reveals_at_chapter}` | 无效 → `None` | 信息差 Commit 2 |
| `anchor_to` | `int ≥ 1` | 无效 → `None`(orchestrator 用 current_arc 兜底) | 锚点归一 Commit 2 |
| `emotion_core` | 7 类之一(憋屈/压抑/爽快/震惊/虐心/甜蜜/燃) | 无效 → `"压抑"` | 情绪锚点 Commit 3 |
| `emotion_intensity` | `int 1-5` | 无效 → `3` | 情绪锚点 Commit 3 |

`_standardize_tasks()`(`outline.py` 模块级 helper)就地兜底,在 `run_outline` 与 `run_outline_card` B/C 分支共享。**审计修 Medium#4**:此前 B/C 分支只 `json.loads(resp)`,跳过所有标准化,选中 B/C 时下游 task 缺字段/章号不连续;修后两个入口共享同一份兜底。

#### 1.3 normalizer 后处理扩展

`backend/engine/agents/normalizer.py` 在 AI 腔处理之外新增 2 类检测:

| 项 | 阈值 | 行为 |
|---|---|---|
| 对话提示词污染(满篇"某某说/道") | `WARNING=25` / `FORCE=50` | 触发预警 / 触发 4 策略替换(动作卡位/神态/情境/语感) |
| POV 视角切换密度 | 默认 `≤ 2`/章 / 多视角章 `≤ 3` | 写进 issues,需用 `【POV 切换 → 角色名】` 显式标注 |

`POV_LOCK_INSTRUCTION`(`prompt_templates.py`)约束 writer:默认第一人称锁定主角,多视角切换必须显式标注,每章 ≤2 次。

**审计修 Medium#1**:对话癌 FORCE 分支原 `dialogue_replace_prompt` 在含 `{污染样本}` 的 f-string 上又 `.format(cnt=)`,章节文本含 `{` 时会抛 `KeyError`;修后全部改纯 f-string。

#### 1.4 节拍校验器 + 12 项验收标准

**节拍校验器**(`backend/engine/tools/beat_checker.py`,离线 CLI)扫 `output/chapters/ch_NNNN_meta.json` 产出红/黄/绿报告:

```
python -m engine.tools.beat_checker <novel_ai_dir> [--window 10] [--save]
```

校验 4 维度:
1. 扮猪吃虎 / 打脸三阶段(铺垫→打脸→碾压,最近 10 章必含 ≥1 次完整三阶段)
2. 升级循环(每 5-10 章必含 升级→反杀+新伏笔)
3. 情绪多样性(最近 5 章 emotion_core 唯一值 ≥ 3)
4. 章末钩子存在(每章 ending_hook_type 必在 7 类钩子内)

退出码:RED=2 / YELLOW=1 / GREEN=0(CI 集成友好)。

**12 项验收标准**(`backend/engine/tools/acceptance_tests.py`,扩展自原 AC-1~AC-5):

```
python -m engine.tools.acceptance_tests all    # 跑 12 项
python -m engine.tools.acceptance_tests ac1    # 单项
python -m engine.tools.acceptance_tests ac6    # 但是法则密度
python -m engine.tools.acceptance_tests ac7    # 信息差多样性
python -m engine.tools.acceptance_tests ac8    # 情绪锚点多样性
python -m engine.tools.acceptance_tests ac9    # 三线分布
python -m engine.tools.acceptance_tests ac10   # 扮猪吃虎节拍(复用 beat_checker)
python -m engine.tools.acceptance_tests ac11   # 升级循环合规(复用 beat_checker)
python -m engine.tools.acceptance_tests ac12   # 对话提示词密度(复用 normalizer 阈值)
```

AC-1~AC-5 沿用原版(设定一致性/题材切换/任务单质量/平台字数钩子/角色弧一致性),AC-6~AC-12 是本轮新增。无数据时全 SKIP → True。

### 2. CLAUDE.md 合规性自检

战略审视原报告中识别出 5 项"看似合理但实际违规"的建议,**均按合规改写执行**:

| 原报告建议 | CLAUDE.md 红线 | 改写后方案 |
|----------|----------------|----------|
| 新增 `EntityStateTimeline` 表 | "未经任务明确授权,不增加数据库表" | 不加表;新字段全部走 `ChapterTask` TypedDict `NotRequired`,无 DB schema 变更 |
| 新增 `plot_engineer` 独立 LangGraph 节点 | "不修改核心 Agent prompt 或 LangGraph 拓扑" | 不动拓扑;反转/节拍校验在 normalizer + beat_checker + acceptance_tests 实现 |
| Neo4j Python embedded 嵌入 | "不增加依赖" | 不引入;复用现有 `L2 cold.world_events` JSON 列表 |
| 番茄签约 API 自动报送 | "不增加公共 API" | 不做 API;走前端按钮 + 用户复制粘贴流程 |
| 反洗稿自检服务化 | "不增加公共 API" | 仅离线 CLI(`fingerprint_checker.py`) |

### 3. 审计修 3 处真实缺陷(2026-07-26 子代理审计)

战略审视 17 commit 交付后,跑子代理详细审计,发现 3 处必修缺陷,均在 2 个聚焦 commit 内修复:

| # | 等级 | 问题 | 修复位置 |
|---|------|------|----------|
| 1 | 🔴 严重 | `orchestrator.save_chapter` 写 meta.json 不含 `shuang_type`/`ending_hook_type`/`emotion_core`/`foreshadowing_ops` 等 → `beat_checker` 与 AC-10/AC-11 真实链路恒为 YELLOW(空转) | `orchestrator.py:749` + `bootstrap.py:225` meta dict 补 7 字段;`.get(...,'')` 兜底 |
| 2 | 🟡 中等 | `normalizer.run_normalizer` FORCE 分支的 `dialogue_replace_prompt` 在含 `{污染样本}` 的 f-string 上又 `.format(cnt=)` → 章节文本含 `{` 时 `KeyError` | `normalizer.py:184-200` 全部改纯 f-string,去掉 `.format()` |
| 3 | 🟡 中等 | `run_outline_card` B/C 分支只 `json.loads(resp)`,跳过 stakes/emotion/章号契约所有标准化 → 选中 B/C 时下游 task 缺字段/章号不连续 | 抽 `_standardize_tasks(tasks, start_chapter)` helper,`run_outline` + `run_outline_card` 共享;顺手修 `_mark_arc_climax` 短弧(<3 章)IndexError 隐患 |

每次修复都配套真实链路回归测试:

- **Critical#1** — `test_meta_save_beat_link_2026_07_26.py`(10 测试):`save_chapter` → 读回 meta.json → `beat_checker` 字段非空 → 4 维度节拍 GREEN → AC-10/AC-11 端到端 PASS
- **Medium#1** — `test_normalizer_force_branch_2026_07_26.py`(9 测试):污染样本含 `{` / `}` / JSON 字面量 / Python f-string 模板 / 未闭合 `{` 都不崩;FORCE 分支调 LLM(WARNING-only 不调,needs_llm=True 不二次调)
- **Medium#4** — `test_outline_card_standardize_2026_07_26.py`(7 测试):`_standardize_tasks` 单元 + B/C 分支标准化 + 章号重编号

子代理审计剩余的 4 项轻微问题(naming/注释/dead code)可择期清理,不影响功能。

### 4. 用法(开发者视角)

### 4.1 写新章节(无 LLM 改动)

正常使用流程不变(`bridge run` 调引擎)。**唯一新增**:outline → writer 链路自动携带方法论指令与 7 字段,无需手动配置。

### 4.2 跑 30 章后做节拍/验收审计

```bash
# 1. 节拍校验(扮猪吃虎/升级循环/情绪多样性/钩子存在)
python -m engine.tools.beat_checker backend/data/engine --window 10
# 输出红/黄/绿 + 详细 details

# 2. 12 项验收标准
python -m engine.tools.acceptance_tests all
# 输出 12/12 通过或各 FAIL 详情

# 3. 单项跑(排查特定问题)
python -m engine.tools.acceptance_tests ac6   # 但是法则密度
python -m engine.tools.acceptance_tests ac7   # 信息差多样性
```

### 4.3 自定义方法论强度(高级)

如果某章/某弧想关闭某招方法论,可在 task 加 `disable_methodology=["but_law"]` 列表(待 `_standardize_tasks` 读;当前未在 outline schema 中暴露,需手动改 task JSON)。终章默认只剩 `three_layer_hook`。

### 4.4 跑 pytest 回归

```bash
# 行为测试(2 独立 pytest 进程,见 CLAUDE.md)
pytest backend/tests --ignore=backend/tests/invariants
pytest backend/tests/invariants
```

新增回归覆盖(本轮 138 个 战略审视回归 + 26 个 审计修回归 = **164 测试**):

| 文件 | 测试数 | 范围 |
|---|------|------|
| `test_methodology_prompts_2026_07_25.py` | 15 | 4 招方法论常量 + helper |
| `test_writer_world_setting_none_2026_07_25.py` | 5 | world_setting=None 兼容 |
| `test_chapter_task_stakes_dilemma_2026_07_25.py` | 9 | stakes/dilemma 字段渲染 |
| `test_chapter_task_methodology_fields_2026_07_25.py` | 11 | narrative_thread/info_asymmetry/anchor_to 渲染 |
| `test_chapter_task_emotion_anchor_2026_07_25.py` | 12 | emotion_core/intensity 渲染 |
| `test_beat_checker_2026_07_25.py` | 18 | 4 维度节拍校验 + load/save/print |
| `test_normalizer_dialogue_cancer_2026_07_25.py` | 19 | 对话癌阈值 + 4 策略 |
| `test_pov_lock_2026_07_25.py` | 16 | POV_LOCK_INSTRUCTION + detect_pov_switching |
| `test_acceptance_tests_12_2026_07_25.py` | 31 | AC-1~AC-12 |
| `test_meta_save_beat_link_2026_07_26.py` | 10 | 审计修 Critical#1 真实链路 |
| `test_normalizer_force_branch_2026_07_26.py` | 9 | 审计修 Medium#1 |
| `test_outline_card_standardize_2026_07_26.py` | 7 | 审计修 Medium#4 |

历史规划、审计过程与提交拆分不在活跃文档重复维护；需要追溯时使用 Git。当前能力、入口和约束以上述章节及源码为准。