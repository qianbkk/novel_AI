# Changelog

This file records release-level behavior changes. Individual fixes and implementation details remain available through `git log`.

## v1.0.1 — UX 架构修复：6 个真实问题（2026-08-18）

设计动机（docs/wiki/09-Architecture-Audit-2026-08-18.md）：用户在真实使用中发现的 6 个问题，每个背后都是一类架构层缺陷（操作前不可知 / 状态不可见 / 一次性黑盒 / 用户旅程断裂）。本版本用架构方案一次性把同模式的所有问题修掉，不是字面 bug 修补。

### Added — 架构级新组件

**1. 后端 LLM 健康检查（核心）**
- `GET /providers/health`：返回 `mode`（mock/live）+ `can_run_llm` + 3 角色（structured_logic / creative_detail / consistency_check）路由明细 + DB provider 列表 + 失败原因
- `POST /providers/{id}/test`：单 provider 联通测试（带超时、上游错误透传）
- 端点签名无 `Depends(get_db)`（避免 owner 校验污染 meta 端点）
- 5 个测试覆盖 mock/live/4xx/404 路径（`tests/test_provider_health_2026_08_18.py`）

**2. 前端 LLM 状态 banner**
- `components/LLMStatusBanner.tsx`：折叠态一行 chip，展开显示 3 角色路由详情
- 不可用时显示「去配置供应商 →」按钮跳 `/settings/providers`
- 接入页面：WorldBuild / Outline / ThemeOpening / NewProject / BridgeConsole
- 6 个页面中 5 个在进入页面时就显示 LLM 状态

**3. 前端后端健康灯**
- `hooks/useBackendHealth.ts`：每 5s 探测 `/health`，三态（checking / up+latency / down+error）
- `App.tsx` sidebar 底部 `BackendStatusBadge` 实时显示
- down 时可展开看错误详情 + 「dev.bat start-all」提示

**4. 前端写作旅程 stepper**
- `Dashboard` 项目卡嵌入 `WritingJourney` 组件
- 5 步（创建 → 题材+主题 → 世界 → 大纲 → 写章节），当前所在步骤高亮
- 项目卡点击跳「当前步骤」对应页（不再固定跳 WorldBuild）

### Fixed — 6 个真实问题

| # | 问题 | 根因 | 修复 |
|---|------|------|------|
| 1 | dev.bat 启动脚本中文乱码 | launcher 启动 python 时未设 `PYTHONIOENCODING=utf-8`，python 按 CP_ACP 写中文到日志，dev.bat tail 用 UTF-8 读乱码 | `dev.bat` launcher 加 `set PYTHONIOENCODING=utf-8` + `PYTHONUNBUFFERED=1` |
| 2 | Dashboard 项目卡复选框与项目名重叠 | `.project-card__title` 无 padding-left | styles.css 加 `padding-left: 60px` |
| 2 | 筛选逻辑漏洞百出（筛 genre1 看不到 genre2） | 题材 chip 来源是 `projects.map(genre)`，filter 掉 genre2 后再点不回 | 引入 `availableGenres` 独立题材池，额外拉一次不带筛选的 list |
| 3 | 创建小说后「开始构建」失败无可知性 | 前端没有任何方式提前知道 LLM 是否就绪 | 见 Added #1 / #2 |
| 4 | 旧项目「生成大纲」失败同上 | 同上 | 同上 + Outline 进入页面强制预检 |
| 5 | WorldBuild 看不到内容，怀疑后端没起来 | 前端无后端状态可视化 | 见 Added #3 |
| 6 | 前端布局不合理，小白不知如何使用 | 前端是「功能清单」不是「用户旅程」 | 见 Added #4 + NewProject 加 5 步旅程说明 |

### Changed — 设计决策

- **WorldBuild 「开始构建」按钮在 LLM 不可用时禁用**：避免用户点了之后 30 秒才发现失败
- **NewProject 创建成功后跳 `/theme`（Pre-Production）** 而非 `/worldbuild`：v1.0 设计要求先做题材画像 + 主题 + 黄金三章，再做世界构建
- **Outline / BridgeConsole 重资源操作前预检**：调用前同步探测 LLM 健康状态，失败时给「去配置供应商」引导而非默默吞错

### Tests

- **6 个新测试**（`tests/test_provider_health_2026_08_18.py`）：
  - mock 模式 can_run_llm=True
  - live 模式无 key → can_run_llm=False + 每角色 reason 含 "API key"
  - live 模式全局 key 配齐 + 角色 provider 未配 → can_run_llm=True + 退回全局
  - DB provider 无 api_key → test 端点 400
  - DB provider api_base 不通 → test 端点 502 + 错误透传
  - 不存在 provider_id → 404
- 前端 build 验证：`npm run build` 通过（62 modules，360KB JS / 84KB CSS）

### Migration Notes

- 无 DB schema 变更
- 无新增环境变量
- 无新增依赖
- 旧项目无需任何改动即可获得新 banner + stepper

---

## v1.0 — 质量优先长篇网文写作架构（2026-08-18）

设计动机（docs/drafts/v1-quality-first-design.md）：v0.5 流水线长、成本高、质量没有匹配。v1.0 走"前期工程深度 + 单章质量"路线，把 1 次前期深度调用变成 5x 写作补救调用的杠杆。

### 核心范式转换

- v0.5：1 次 outline LLM 调用 → 9 个 agent 多轮循环（writer/normalizer/compliance/checker×3/rewriter/tracker）→ 单章 5-9 次 LLM
- v1.0：**前期工程前置**（Pre-Production 4 个 agent）+ **大纲脊柱化**（macro_spine）+ **writer prompt v2 注入全部前期成果**（4 个 block）+ **单轮聚焦质检**（escalate 不 rewrite）+ **3 个 memory ledger 贯穿**

### Added — 9 个新模块

**Stage 1 Pre-Production（4 个）**：
- `genre_profiler.py` + `config/genre_profiles.py`：6 个主流男频（玄幻/仙侠/都市/历史/军事/科幻）的 reader_persona + tone + taboo + show_item_examples + research_strength 三档分流
- `theme_designer.py`：6 题材的 theme_statement + expectation_arc (seed< twist < payoff) + resonance_anchors ≥3 共性维度
- `opening_designer.py`：6 题材的黄金三章（ch1 锚定 → ch2 问题 → ch3 翻转），hook_type 严格 7 个合法之一，含 show_item_seed/used 接力
- `research_notes.py`：按 research_strength 三档分流（strong = 5 维度朝代/地理/职官/物价/服饰 baseline；medium = system_consistency；weak = minimal），query API 按章返回

**Stage 2 大纲**：
- `macro_spine.py`：全书宏观弧（arc 边界连续、twist_chapter 必须落在某 arc 范围内），get_arc_for_chapter 写每章前调

**Stage 3 Writer Prompt v2**：
- `_build_genre_block` / `_build_theme_block` / `_build_expectation_block` / `_build_showitem_block`：4 个新 block 注入 writer prompt

**Stage 3 质检**：
- `scene_quality_check.py`：4 维度（expectation_advanced / show_item_landed / resonance_hit / consistency_ok），任一失败 → escalate 给人工（**不自动 rewrite**，v1.0 决策），LLM 失败抛 SceneQualityCheckFailed（不让 silently PASS）

**Stage 4 Memory**：
- `memory/expectation_ledger.py`：每章 expectation_status 落盘，get_pending_seeds 给下章追踪
- `memory/show_item_chain.py`：每章 show_item_used 接力，get_recent_items 给 writer prompt v2 用
- `memory/voice_anchors.py`：角色口癖记录 + check_voice_consistency 一致性检查

**API 8 个 endpoint**（`/projects/{id}/pre-production/{genre-profile,theme,opening,research-notes}` × GET/PUT/POST-generate）：
- GET 不存在 → 404；PUT 缺字段 → 400；PUT 强制 source='user'（不被 LLM 覆盖）

**Frontend 1 个新页面**：
- `pages/ThemeOpening.tsx`：4 个 tab（题材画像/共性主题/黄金三章/资料助手），每个 tab View ↔ Edit JSON 状态切换
- 路由 `/projects/:projectId/theme`

### Changed

- **writer prompt v2**：4 个新 block（genre / theme / expectation / show-item）注入【当前写作任务】之前；向后兼容（无 v1_* 字段仍工作）
- **paths.py**：新增 `novel_ai_dir(novel_id)` helper（env-aware，测试可 monkeypatch）

### Tests

- **16 个新测试文件，145 个新测试**：
  - genre_profiler (27) + theme_designer (14) + opening_designer (14)
  - pre_production_api (16) + research_notes (16) + macro_spine (15)
  - writer_prompt_v2 (11) + scene_quality_check (11) + memory_ledgers (9)
  - v1_e2e_pipeline (6) — 全链路串联验证

### 向后兼容

- v0.5 调用方式（无 v1_* 字段）仍正常工作（writer prompt v2 兼容测试通过）
- 9 个 v0.5 修复（PASS_SCORE_GOLDEN / normalizer 响亮化 / lorebook aliases / RAG cold_history 等）保留
- 全套基线：**1627 behavior tests pass**（13 个 GBK baseline 无新增回归）

### 真实 LLM 端到端

完整 30 章真实跑需在用户本地（带 MiniMax API key）执行 `python -m scripts.drive_30ch_bridge <project_id>`。Mock 版 e2e 验证所有模块串联。

## v0.5.0 — 质量门强化与一致性兜底（2026-08-17）

5-Agent 并行审计识别了 P0/P1/P2/P3 共 18+ 项真实缺陷，16 个聚焦 commit 一一落地，每个 commit 都"先复现测试 → 最小实现"。

### Added

- **`PASS_SCORE_GOLDEN` / `GOLDEN_CHAPTER_COUNT` 常量**：前 N 章走黄金评分阈值，避免黄金章节落盘时被普通阈值拦下。
- **writer prompt 6k 字硬上限**：`WRITER_PROMPT_BUDGET_CHARS` + 截断 warning，防止 token 爆炸拖垮 LLM 守约束率。
- **`summarizer_metrics` / `memory_gaps` OrchestratorState 字段**：从源头声明，杜绝状态静默丢字段。
- **`RECENT_SUMMARIES_INJECT_COUNT = 10`**：writer prompt 注入近期摘要从 5 提到 10，30 章后的回望更稳。
- **plot_skeleton dual-format fallback**：`arc-form` 与 `volume-form` 双形态解析，254c724 残留不再造成静默丢大纲。
- **`Character.status` / `died_in_chapter` 字段 + alembic 0004 迁移**：追踪已亡角色，阻断 dead-character-OOC 复活。
- **RAG `cold_history` 召回**：长程记忆摘要进向量库，长篇一致性不再断在十几章后。
- **lorebook aliases 扩源**：角色 `speech_quirks` + `aliases` 字段并入 lorebook aliases 列表，让口癖触发能召回角色卡。
- **`talk_questions` API 暴露**：talk 模式不再"模式死端"，前端可消费。
- **`cosine_similarity_with_warning`**：embedding 维度漂移触发 warning，不再静默失效。

### Changed

- **normalizer DIALOGUE_TAGS_PATTERN 扩词表 + alternation 修正**：补喊/喝/笑/喃等高频词；排序改为长词优先，避免短词抢先匹配造成"喊道/答道"漏识别。speaker 排除类防贪婪匹配吞掉替代首字符。
- **`pull_setting` Pydantic ValidationError** 由 `log.warning` 改 `raise`：8 段卡空设定不再静默 fallback。

### Fixed

- **P0 NameError 阻断**：`PASS_SCORE_GOLDEN` 未定义导致黄金章节写崩。
- **P0 prompt 专名泄漏**：7 处 HOOK_TYPES 例 + 1 处都市线 + 1 处标题示例 + 2 处 fallback 默认值（陆承 / 感债者）改为中性措辞。
- **P0 覆盖已完成章节**：`save_chapter` 幂等保护 + `_overwrite` 逃生口。
- **P1 normalizer 静默 fallback**：失败响亮化，issue 入栈并 escalate。
- **P1 schema 静默丢字段**：`OrchestratorState` 声明 `summarizer_metrics` / `memory_gaps`。
- **P1 outline placeholder 污染**：card/talk 模式空候选硬失败。
- **P1 embedding 维度静默失效**：维度漂移 warning。
- **P1 init_arc 单形态崩**：254c724 残留兼容。
- **P1 writer prompt 守约束率塌方**：6k 字硬上限 + warning。
- **P2 Pydantic raise** 防 8 段卡空。
- **P2 talk 模式死端**：API 暴露 talk_questions。
- **P2 已死复活 OOC**：Character.status + death keyword 规范。
- **P2 长程一致性崩塌**：RAG cold_history 召回。
- **P2 口癖漂移**：lorebook aliases 扩源。
- **P3 normalizer 满篇"某某说/道"落盘**：dialogue tags 补全 + alternation 修正。

### Tests

- **15 个新测试文件，~95 个新测试**：覆盖每个 P0/P1/P2/P3 修复的真实链路。
- **2 个老测试更新**：当旧断言锁定的是 bug（normalizer / Character.status），按 CLAUDE.md 规则改成断言正确行为并写明原因。
- 全套基线：1483 backend behavior tests pass / 519 invariants pass / compileall clean / frontend build clean（12 个 baseline GBK 编码失败与改动无关）。

## Unreleased

### 战略审视 7 项 backlog 全部交付（2026-07-25 → 2026-07-26）

基于真实 30+ 章 LLM 跑批后的战略审视,识别的方法论密度 gap 全部在 17 个聚焦 commit 内落地,审计子代理发现的 3 处真实缺陷在 2 个修复合并 commit 内修复。

#### Added — 方法论与字段

- **4 招方法论内化**(`prompt_templates.py`):`INFO_ASYMMETRY_INSTRUCTION`(信息差三模式) / `BUT_LAW_INSTRUCTION`(章首+章中+章尾转折) / `THREE_LAYER_HOOK_INSTRUCTION`(微观/中观/宏观) / `MODULAR_NARRATIVE_INSTRUCTION`(主/支/暗线)+ `get_methodology_instruction()` helper。writer prompt 默认全开,终章仅保留 3 层钩子。顺手修 `writer.py:296` 原 `{{}}` f-string bug(set literal + dict 元素 → `TypeError`)
- **7 个 ChapterTask 结构化字段**(`state.py:ChapterTask` 通过 `NotRequired[...]` 增量加):`stakes` / `dilemma` / `narrative_thread` / `info_asymmetry` / `anchor_to` / `emotion_core`(7 类之一)/ `emotion_intensity`(1-5)。老 task JSON 自动兼容
- **POV_LOCK_INSTRUCTION**(`prompt_templates.py`)+ `normalizer.detect_pov_switching`:第一人称锁定主角,每章 ≤2 次切换,需 `【POV 切换 → 角色名】` 显式标注
- **`engine/tools/beat_checker.py`** 离线节拍校验器:扮猪吃虎三阶段 + 升级循环 + 情绪多样性 + 钩子存在性,扫 `ch_NNNN_meta.json` 产红/黄/绿报告,退出码 RED=2/YELLOW=1/GREEN=0(CI 友好)
- **`engine/tools/acceptance_tests.py` 5→12 项**:AC-1~AC-5 原版保留,新增 AC-6(但是法则密度)/ AC-7(信息差多样性)/ AC-8(情绪锚点多样性)/ AC-9(三线分布)/ AC-10(扮猪吃虎节拍)/ AC-11(升级循环)/ AC-12(对话提示词密度)

#### Changed

- **normalizer 对话提示词阈值**:5 → 25(预警)/ 50(强制 4 策略替换:动作卡位/神态/情境/语感)。原报告阈值错
- **`_standardize_tasks()` 抽出 helper**:`run_outline` 与 `run_outline_card` B/C 分支共享同一份字段兜底 + 章号契约;顺手修 `_mark_arc_climax` 短弧(<3 章)IndexError 隐患
- **`outline.py` prompt schema** 加 7 字段填写指引 + 7 钩子/7 爽点 enum

#### Fixed — 审计子代理发现的 3 处真实缺陷

- **🔴 Critical#1 meta.json 落盘断层**:`orchestrator.save_chapter`(line 749)+ `bootstrap.py` 写 meta dict 不含 `shuang_type`/`ending_hook_type`/`emotion_core`/`foreshadowing_ops`/`is_arc_climax`/`narrative_thread`/`emotion_intensity` → `beat_checker` 与 AC-10/AC-11 真实链路恒为 YELLOW(空转)。`.get(...,'')` 兜底,老 task 也能写
- **🟡 Medium#1 normalizer `.format()` 叠加 f-string 崩溃**:`dialogue_replace_prompt` 在含 `{污染样本}` 的 f-string 上又 `.format(cnt=)`,章节文本含 `{` 时 `KeyError`。全部改纯 f-string
- **🟡 Medium#4 outline card B/C 跳过标准化**:同上,修法见 Changed 段

#### Tests

- **138 个战略审视回归测试**(`backend/tests/test_*_2026_07_25.py`):覆盖 4 招方法论 + 7 字段渲染 + beat_checker + 对话癌 + POV 锁定 + 12 项验收
- **26 个审计修回归测试**(`backend/tests/test_*_2026_07_26.py`):覆盖 Critical#1 真实链路 + Medium#1 FORCE 分支 + Medium#4 card 标准化
- 新增测试 fixture 设计原则:`isolated_novel_ai_dir` / `isolated_chapters_dir` 把 `paths.py` 重定向到 `tmp_path`,不污染真实 `NOVEL_AI_DIR` 数据;FORCE 分支 mock 在 `normalizer` 模块的 `LLMRouter` / `get_active_router` 名字上,覆盖模块级 + 函数级 import 两条路径

### Documentation

- [03-Writing-Engine.md](docs/wiki/03-Writing-Engine.md) § 方法论内化与节拍校验:把原"已知方法论 gap 与补全计划"待办段全部替换为已交付能力清单 + 审计修 3 处缺陷 + commit 映射表
- [06-Dev-Setup.md](docs/wiki/06-Dev-Setup.md) 测试段加 beat_checker / acceptance_tests 12 项 CLI 用法
- [07-Real-LLM-Testing.md](docs/wiki/07-Real-LLM-Testing.md) § 7.1 战略审视后必跑步骤 + § 7.2 改了什么清单
- [00-Home.md](docs/wiki/00-Home.md) 工程化基线更新(75/100 → 含战略审视 17 commit + 审计修 2 commit)

### 工程化提升（2026-07-24 → 2026-07-25）

本阶段把 63/100 的工程化基线推到 75/100。10 个 commit 覆盖 P0 全部 6 项 + P1 全部 3 项 + 1 项 /simplify 高 ROI 修复 + 2 项 /code-review critical bug 修复。

#### P0 — 必修（6 项全部完成）

| 短板 | 修复 | Commit |
|------|------|--------|
| 404 兜底 + ErrorBoundary 缺失 | NotFound.tsx + ErrorBoundary.tsx class 组件，路由 `<ErrorBoundary key={pathname}>` 包 `<Routes>`，404 path="*" 兜底 | `656b2be` |
| API client 缺 abort/timeout/retry | `RequestOptions { signal/timeoutMs/retries }` + `withTimeout` 5 行 + retry 退避 + `withConcurrency` sliding window + `useSafeAsync` hook | `ad9d8f2` |
| 无 CI / lint / pre-commit | GitHub Actions 双 job（backend pytest + frontend lint/typecheck/build）+ ruff 0.6.9 (devDep) + pre-commit hooks | `5eb3033` |
| BridgeRun TOCTOU 并发漏洞 | partial unique index `WHERE status IN ('pending','running')` 数据库层硬保证 + pre-cleanup 归档孤儿 + 3 个新测试 | `3b80b3c` |
| app↔engine 双向 import | 抽 `shared/atomic_io.py` + `shared/setting_schema.py`，原模块 re-export shim 兼容（消除 4/8 双向 import） | `ccdae15` |
| 核心链路裸 dict | 11 个 Pydantic v2 模型镜像 setting_package.json 字段，setting_sync 入口软验证 | `d5be2d6` |

#### P1 — 应当改（3 项全部完成）

| 短板 | 修复 | Commit |
|------|------|--------|
| 4 个巨型 page 组件 | WorldBuild.tsx 1030 → 751 行：抽 EmptyTab / FactionGraph / WorldviewTab + groupMapByLevel helper | `6283e2b` |
| 212 处 inline style | Dashboard `chipStyle()` JS 函数 → `.genre-chip` CSS class + 6 个 dashboard class，13 处收编 | `3a1afda` |
| FK nullable + ondelete 缺失 | 16 个 FK 加 ondelete（CASCADE/SET NULL）+ Project.owner_id FK + alembic 0003 + 5 个测试 | `e6e1a1d` |

#### 审计优化（/simplify + /code-review）

| 修复 | Commit |
|------|--------|
| /simplify 4 高 ROI：Dashboard 接入 withConcurrency + NotFound/ErrorBoundary copyDebugInfo 合并 + 删 retryOnPost YAGNI + groupMapByLevel inline | `3bb98c4` |
| /code-review 2 critical bug：useSafeAsync signal 永远不被 abort + migrations ROW_NUMBER NULL ordering | `（本次 commit）` |

#### 未做（评估后 skip）

- **alembic 0003 部署验证未跑**：migration 写好但 alembic upgrade head 实际未在生产 DB 执行（FK 约束名匹配、列数对齐、UNIQUE 重复行清理在生产环境需手动验证）。
- **useSafeAsync 落地 10 个 page**：hook 自身 bug 已修，但 10 个 page 仍手写 `mountedRef`，落地工作量大。
- **_build_minimal_card 噪声数据**：60 行把 key_characters 套 8 段留空 "待补全"，需同步改前端 CharacterCard fallback 渲染。
- **Pydantic + JSON Schema 双契约**：184 行 Pydantic 镜像 JSON Schema，需 codegen 单向绑定，工作量大。
- **ErrorBoundary 用 react-error-boundary 库**：167 行 class 改库调用，UI 是产品设计资产，搬过去成本与收益不匹配。
- **frontend retry 删 + 改 Idempotency-Key**：破坏性大。

### Added

- **Dashboard 运行时可见性**（`1ecb726`）：`/projects` 端点附带 `active_run_command/status/id/started_at` 字段；Dashboard 项目卡片显示 ⟳+命令名+呼吸动画 badge，bridge.run 期间可见，点跳 BridgeConsole。
- **`backend/scripts/continue_worldbuild.py`**：续跑卡在 stage 2（MiniMax 突发 429 等）的 draft 项目；跑前清掉上一轮失败的中间产物，每 stage 6 次重试。实测 14 分钟 worldbuild 完成。
- **`backend/scripts/drive_30ch_bridge.py`**：按前端 BridgeConsole 按钮 1:1 跑完整 7 步 pipeline；全程 HTTP API + SSE，不直接调 `engine.tools`。
- **`backend/scripts/backfill_worldbuild.py`**：幂等回灌已有项目（real30ch-16862056 实测 6 char / 5 factions / 4 relations）。
- **真实 31 章跑通**（2026-07-24）：real30ch-16862056 项目从 worldbuild 到 import-chapters 完整跑通，31 章、64,545 字、$0.74 总成本。
- **CI 质量门**（`5eb3033`）：PR/push 自动跑 backend pytest + frontend lint/typecheck/build + ruff（仅改文件）。
- **404 兜底路由**（`656b2be`）：`<Route path="*" element={<NotFound />} />`。
- **路由级 ErrorBoundary**（`656b2be`）：子组件渲染抛错降级到友好错误页 + 复制调试按钮。
- **API client abort/timeout/retry**（`ad9d8f2`）：GET 默认 30s 超时 + 1 次重试，POST 默认 60s 无重试。
- **withConcurrency 滑动窗口**（`ad9d8f2`）：替代 Dashboard 4 并发手写 chunk 循环。
- **useSafeAsync hook**（`ad9d8f2`）：统一 mountedRef + AbortController 模式。
- **BridgeRun partial unique index**（`3b80b3c`）：DB 层硬保证"一个 project 至多一条 active run"。
- **shared/atomic_io**（`ccdae15`）：跨 app↔engine 原子写 helper（解决双向 import）。
- **shared/setting_schema**（`ccdae15`）：跨 app↔engine JSON Schema 校验（解决双向 import）。
- **Pydantic 核心模型**（`d5be2d6`）：11 个 SettingPackage / WorldviewRich / CharacterCard / EntityRelationRich。
- **Genre chip CSS class**（`3a1afda`）：13 处 inline 收编为 .genre-chip + .genre-chip--active。
- **alembic 0003 fk_cascade_unique**（`e6e1a1d`）：16 FK ondelete CASCADE/SET NULL + Project.owner_id FK + ChapterCharacter/Outline UNIQUE。
- **CopyDebugButton 共用**（`3bb98c4`）：NotFound + ErrorBoundary 16 行复制逻辑合并为 1 处。

### Fixed

- **#13 pull-setting 重复人物**：planner 同时把 `protagonist.name` 放进 `key_characters[]` → 同一 name 写 2 行（之前 7 character 含 2 林渊）。`app/bridge/setting_sync.py` `_add_character` 加 `seen_names` 守门。
- **#14 Dashboard 看不到正在跑**：见上"Added"段。
- **P0-1 章节阅读器独立滚动**：`.reader-page` 锁 viewport 高度 + TOC 与正文各自 overflow-y 独立滚动条。
- **P1-3 FK nullable + ondelete**：14 个 project_id FK 加 CASCADE、MapNode.parent_id 加 CASCADE、Foreshadowing.linked_character_id 加 SET NULL、Project.owner_id 加 FK(users.id) nullable。
- **/code-review useSafeAsync signal 泄漏**（本次）：hook 之前在 `controllerRef.current?.signal ?? new AbortController().signal` 每次 render 都 fallback 一个新 controller，cleanup abort 不到。改用 useState 惰性初始化保证 ref 稳定。
- **/code-review migrations NULL ordering**（本次）：`_run_pre_index_cleanups` ROW_NUMBER OVER ORDER BY started_at DESC 在 SQLite 下两条 NULL 行保谁任意。加 `COALESCE(started_at, '1970-01-01') + id DESC` 兜底。

### Documentation

- `docs/wiki/07-Real-LLM-Testing.md` 增补 2026-07-24 实战经验（§8 续跑 draft 项目 + §9 Bridge pipeline 实测数据 + §10 待办）。
- `docs/wiki/00-Home.md` 重写为"阅读顺序 + 维护规则"格式，移除指向已删除 `07-Standalone-Engine.md` 的死链。
- `docs/wiki/03-Writing-Engine.md` 移除对已删除文档的引用。
- `docs/wiki/06-Dev-Setup.md` 新增 `continue_worldbuild.py` / `drive_30ch_bridge.py` 章节。
- `docs/wiki/08-Frontend-Runbook.md` §3.7 新增 Dashboard ⟳ badge 故障排查章节。
- `docs/wiki/ARCHITECTURE.md` 重新定位为"5 分钟速览"（不再是与 00/01 重复的速览）。
- `docs/audit/`、`docs/superpowers/` 空目录清理；历史报告归档至 `docs/runs/_archive/`。

## 2026-07-22


### Added

- Real LLM end-to-end testing experience documented at `docs/wiki/07-Real-LLM-Testing.md`, capturing the 8 issues found during a 30-chapter MiniMax-M3 run and the root-cause fixes that prevent regression.
- Chapter title parsing covers the LLM "semi-legal JSON" case (body contains real newlines) so titles stop coming through as `{"title": ..., "body": ...}` literals in the database, frontend, and per-chapter meta files.
- 30 chapter outputs include a 4-arc plan in the world-build result and project creation/modification timestamps on dashboard cards.

### Changed

- `engine/llm/router.py` retries MiniMax HTTP 529 with 6 attempts and 2-60s exponential backoff and prints each attempt so the bridge no longer appears to hang for up to two minutes.
- `engine/agents/writer.py` extracts a title even when the model wraps the response in JSON, removing the silent fallback that surfaced JSON snippets as chapter titles.
- `engine/orchestrator.save_chapter` re-runs the title extractor before persisting so `ch_NNNN.txt` is always plain body, never the raw LLM response.
- `app/worldbuild/stages.py` deduplicates characters within a single stage by name, removing duplicate "林渊" rows introduced when the mock payload re-emits the protagonist.
- `app/security.py` logs master-key source, length, and sha256 fingerprint on startup so key drift across restarts is immediately visible.
- `engine/workers/run_bridge_subprocess.py` loads `backend/.env` on startup when the parent process did not pass it through, preventing `MINIMAX_API_KEY 未设置` failures in subprocess runs.
- `scripts/preset_worldbuild.py` runs the 10-stage worldbuild against the real LLM by default (was hard-coded mock), populating the eight character-card fields.
- `frontend/src/pages/ChapterReader.tsx` rejects any title that is JSON-shaped or longer than 30 characters, showing "（标题待生成）" instead of leaking the malformed data.

### Fixed

- Frontend project list now shows creation and modification timestamps.
- Bootstrap stage no longer leaves a stale `BridgeRun` in `running` state when stdout is quiet during HTTP 529 retries.
- Stage `characters` no longer writes a duplicate row when the same name appears twice in the LLM output.

### Documentation

- Consolidated active documentation around the architecture Wiki and removed completed audit, benchmark, and phase-plan reports from the working tree.
- Removed duplicate pytest collection through the legacy invariant re-export module and documented the canonical test layout.
- Classified supported maintenance scripts and removed one-off benchmark and migration drivers.
- Archived the older `docs/runs/30ch-real-2026-07-20/` run under `docs/runs/_archive/`; the 2026-07-22 run remains the canonical real-LLM example.

## 2026-07

### Added

- Long-form continuity support: structured worldbuilding snapshots, foreshadowing operations, cross-arc memory inheritance, and final-chapter handling.
- A zero-cost chapter rule checker feeding the six-dimension LLM quality review.
- Multi-user authentication hardening, project ownership isolation, login rate limiting, and production startup validation.
- Atomic persistence, backup/restore support, corruption recovery, and cross-storage reconciliation tools.

### Changed

- Unified the standalone writing engine into `backend/engine` and made the web bridge its supported execution path.
- Split structural invariant tests by domain and made test path discovery independent of the current working directory.
- Standardized the backend development port on `8132` and the frontend development port on `5293`.

For earlier detail, use `git log --all -- CHANGELOG.md` or inspect the relevant source file history.
