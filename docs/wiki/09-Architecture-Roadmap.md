# 长篇质量架构路线图

本文是**执行文档**：给后续开发者/AI 用的施工图。它回答一个问题——当前架构还差什么，才能稳定产出高质量的长篇网络小说（50 章验证，几百章可扩展）。

阅读前置：[01-Architecture](01-Architecture.md)（进程边界）、[03-Writing-Engine](03-Writing-Engine.md)（写作回路）、[07-Real-LLM-Testing](07-Real-LLM-Testing.md)（真实 LLM 踩坑）。

文中所有"现状"都附了代码位置，施工前请先核对——代码会变，结论要重新验证过才能用。

---

## 一、判断标准：长篇是怎么垮的

短篇看文笔，长篇看**衰减曲线**。三种失效模式，按杀伤力排序：

| 失效模式 | 表现 | 出现时机 |
|---|---|---|
| **一致性衰减** | 人名/境界/地名/前文事实对不上；同一角色前后两副性格 | 20 章后显现，50 章后失控 |
| **结构失控** | 大纲是第 0 章拍的，剧情早就长歪了，骨架却不动；伏笔只埋不收 | 30 章后显现 |
| **质量不可测** | 质量门给的分和真实好坏无关，于是所有自动重写都在优化错误目标 | 从第 1 章起就存在，但要靠人读才发现 |

网文实践里还有一层：**追读率**由前三章和每章结尾钩子决定，但**弃书**由一致性和伏笔回收决定。前者本项目已有机制（黄金三章、钩子类型、情绪锚点），后者是当前的短板。

下面按这三条逐一核查现状。

---

## 二、现状核查

### 2.1 一致性：检索层三缺一，且缺的那层存错了

写作上下文当前由三部分构成：

| 层 | 作用 | 状态 |
|---|---|---|
| L2 摘要（热/冷/约束） | 时间近邻——最近发生了什么 | ✅ 完整，`engine/memory/manager.py` |
| 关键词世界书 | 实体精确——提到谁就给谁的设定原文 | ✅ 已接入 `writer._build_lorebook_block()` |
| 向量检索 | 语义模糊——几十章前那件相关的事 | ⚠️ **写入通、读取不通、粒度错** |
| 实体图谱 | 关系推理 | ⚠️ DB 有表，引擎零引用 |

向量检索的问题比"没接线"更深：

- `app/rag/retrieval.py:76-85`：**整章 embed 成一个向量**。一章 2000–3000 字压成单个向量，语义被平均掉，检索出来只能告诉你"这章大概相关"，没法定位到具体段落。
- `retrieval.py:82`：`text_snippet=content[:200]`，**只存前 200 字**。就算检索命中了，能拿回来喂给 writer 的也只有开头 200 字——而开头往往是承接上章的过场，恰恰不是命中的那段内容。
- `semantic_search_chapters`（`retrieval.py:111`）唯一的消费者是 `app/api/chapters.py:86` 的前端搜索框。**引擎侧零引用**。

也就是说：embedding 表一直在写（`chapter_import.py:285` 每导入一章就 embed 一次），存的是没法用的形态，然后没人读。

**实体图谱**同理：`Character` / `CharacterRelation` / `MapNode` 表都有数据（`pull-setting` 灌入），但那是给前端展示用的，引擎从不查询。

### 2.2 结构：骨架浇筑之后再也没动过

- `arc_plans` 由 planner/`init_arc` 生成后存进 state（`orchestrator.py:220` 只读不写），**全程不修订**。
- `setting_package.json` 只有引擎的 planner 写（全仓库确认：`app/` 侧只读不写）。写作循环跑到第 50 章，setting 还是第 0 章那份。
- 每弧生成细纲时 `run_outline(arc, start, setting, memory)` 会读 L2（`orchestrator.py:261`）——**这是现有架构里唯一的反馈通路**，值得保留和放大。但它只影响本弧的章节任务，改不了 arc 骨架，也改不了 setting。

一条相关的架构断裂（当前**潜在**、尚未爆发）：`setting_package.json` 是单向的 引擎→DB。前端目前对角色/世界观**只有 GET**（`client.ts` 里 `listCharacters` / `getCharacterCard` / `getRelationsGraph` 全是只读），所以还没出问题。**但只要给前端加上"编辑角色卡"，这个编辑就永远传不回引擎**——做那个功能之前必须先建回写通道。

### 2.3 质量门：拿不到证据却要判案

`engine/agents/checker.py:78-86`，checker 的 user_prompt 只有三样东西：

```
【章节信息】第N章 | 定位 | 爽点
{rule_feedback}
【章节正文】{sample}
```

没有 setting，没有 L2，没有上一章。但评分维度里：

- `consistency`（设定一致性，**15%**）——"力量体系/世界观/人物关系/前文事实是否一致"
- `plot_logic`（情节逻辑，**15%**）——"与前文是否矛盾"

**合计 30% 的权重，压在模型根本无从判断的两个维度上。** 模型只能靠语感瞎猜，猜出来的分还参与 `PASS_SCORE` 判定和自动重写决策。

校准这条线也是断的：

- `PASS_SCORE = 6.5`（`orchestrator.py:67`）是硬编码常量。
- `engine/tools/calibrate_checker.py:182` 把校准结果写进 `reports/calibration_result.json`，**唯一的读者是同文件的 `cmd_report()`**（打印给人看）。没有任何代码消费它。
- 而且校准测的是"能否区分人写/AI 写"（`calibrate_checker.py:138`：human 判 ≥7.0 算对，AI 判 ≤5.0 算对），**不是"6.5 这条线划得对不对"**。两回事。

### 2.4 writer prompt：指令过载

`engine/agents/writer.py` 的 user_prompt 有 **24 个 `【】` 指令块**（世界观速览、世界书、一致性硬约束、风格参考、筹码、两难、叙事线、信息差、锚点、情绪锚点、主角状态、上章结尾、近期事件、剧情线、人物状态、爽点、出场人物、禁止事项、到期伏笔、历史背景、输出格式……）。

指令数量超过一定量后，模型的遵循率会显著下降，且**下降是不均匀的**——排在后面的、抽象的约束先被丢掉。当前"严禁吞设定""伏笔必须回收"这类关键约束的位置需要重新审视。

这一条**不要凭直觉改**，见 §4.5 的验证方法。

---

## 三、架构主张

### 主张 1：检索分三级，各司其职，不是三选一

- **L2 摘要** 管时间近邻
- **世界书** 管实体精确（提到"魔纹"就给魔纹的设定原文）
- **向量检索** 管语义模糊（"主角上次面对背叛是怎么反应的"）

三者互补。当前缺的第三级，要修的**不是接线，是存储粒度**：必须按场景切块、存全文、带章节号回指。

### 主张 2：结构化世界数据的共享，靠扩展文件契约，不靠让引擎连库

`CLAUDE.md` 定死了引擎以子进程运行、只通过绑定目录的 JSON/TXT 与后端同步。这个约束是对的（进程隔离让引擎能独立长跑、崩溃不拖垮后端），**不要为了方便去破坏它**。

正确做法：沿用既有的 `push-concept` / `pull-setting` 通道，**加一个方向的文件契约**，而不是新建一套 RPC。

### 主张 3：设定回流做成"事件驱动的增量修订提案"，不是"定期全量重生成"

全量重生成会放大 AI 自我漂移——每次重写都在上一次的偏差上再偏一点，几百章后面目全非。

正确做法：
- **触发**：弧结束（已有 `run_summarizer("arc_end", ...)` 钩子，`orchestrator.py:797`）或累计 N 章
- **输入**：L2 的 `established_facts` / `character_states` / 新出现的地名势力
- **输出**：对 setting / arc_plans 的**差异提案**（diff），不直接覆盖
- **落点**：提案进 `human_pending`，前端可审——复用项目已有的人机协作模式

**可扩展性是本轮的重点**：定义 reviser 契约，本轮只实现一种（角色状态回流），后续加势力/地图/大纲只是增加 reviser 实现，不动框架。这正是用户要求的"几百章后才显现，但现在要有可扩展性"。

### 主张 4：要么给质量门证据，要么别假装在评那一维

`consistency` 和 `plot_logic` 必须附上判断依据（世界书命中的设定原文 + 上章结尾 + 近期摘要），否则就把这 30% 权重重新分配。**不能保持现状**——现状是拿噪声当信号。

### 主张 5：校准要闭环到阈值

校准的产出应该是 **`PASS_SCORE` 的推荐值**，而不只是准确率报告。`PASS_SCORE` 从常量改成"读校准结果，缺失时用默认值并**明确告警**"（符合 CLAUDE.md 的"失败要响亮"）。

---

## 四、施工任务

顺序有依赖，**按 A → B → C 走**。每项都按 CLAUDE.md 的规矩：先查真实调用链 → 再写能复现问题的测试 → 再做最小实现。

### Phase A：架构补完（不需要真实 LLM，全部可用 mock 验证）

#### A1. 向量检索改造 + 引擎接线

**证据**：`app/rag/retrieval.py:76-85`（整章一向量）、`:82`（snippet 200 字）、引擎零引用。

**改法**：
1. `EmbeddingChunk` 按**场景块**切分入库，不再整章一条。切分规则建议：按空行/场景转换切，目标 300–500 字/块，块间重叠 1–2 句防止切断因果。保留 `chapter_no` 和块序号。
2. `text_snippet` 存**块全文**（300–500 字，可直接喂 writer），不是 `content[:200]`。
3. 引擎侧新增检索入口。**关键约束**：引擎是子进程、不连 DB，所以走绑定目录——由 backend 在 `import-chapters` 时把切块结果写成绑定目录下的检索索引文件，引擎读文件做检索。**不要让引擎 import SQLAlchemy**。
4. writer 上下文新增一块【往事回响】：用本章任务 + 近期事件做查询，取 top-2~3 块，总量受预算控制（参考 `LOREBOOK_BUDGET_CHARS` 的做法）。
5. embedding 用现有 `app/rag/embedding.py`——**已有 mock fallback（bigram 哈希），无需新依赖、无需 API key 即可跑通**。

**验收**：
- 切块后同一章产生多条 chunk，块内容可完整取回
- 检索命中的是**语义相关的那一块**，不是整章第一段
- 查询无命中时注入空块，不影响写作（降级不阻断）
- 预算上限生效
- 维度不一致时 `cosine_similarity` 返回 0.0 的 fail-safe 行为不被破坏（`embedding.py:115`）

**风险**：历史数据是整章向量，改切块后新旧混存。需要迁移脚本或明确的重建入口，且**必须与旧数据兼容**（CLAUDE.md 不变量）。

#### A2. canon 契约 + 设定回流骨架

**证据**：`arc_plans` / `setting_package.json` 全程不修订（§2.2）。

**改法**：
1. 定义 **reviser 契约**：
   ```
   propose(memory: dict, setting: dict, state: dict) -> list[Revision]
   Revision = {target, path, current, proposed, evidence, confidence}
   ```
   `target` ∈ {setting, arc_plan}；`evidence` 必须指回 L2 里的具体事实或章节号——**没有证据的提案一律丢弃**，这是防漂移的关键闸门。
2. 本轮只实现 **一个** reviser：`character_state_reviser`——把 L2 `hot.character_states` 里稳定下来的状态与 `setting.key_characters` 比对，产出差异。
3. 触发点挂在弧结束（`orchestrator.py:797` 附近，`run_summarizer("arc_end", ...)` 之后）。
4. 提案**不直接写 setting**，进 `human_pending`（`task_type: "confirm_revision"`），走既有 review 通道（`app/bridge/reports.py:apply_review`）。
5. 前端在写作控制台展示提案并可接受/拒绝。

**验收**：
- 无证据的提案被丢弃
- 提案不改变 `setting_package.json`，直到被 accept
- 重复触发幂等（同一条不重复提案）
- reviser 抛异常时降级为"本弧无提案"，不阻断写作
- 新增一个 reviser 只需实现契约、注册，不改框架——**用一个假的第二 reviser 测试这一点**

**风险**：这是新的写路径，务必复用既有原子写入；中断/重跑不得覆盖已完成章节（CLAUDE.md 不变量）。

#### A3. 给 checker 判案证据

**证据**：`engine/agents/checker.py:78-86`，30% 权重的两维无依据（§2.3）。

**改法**：
- `score_chapter` 增加可选的证据参数：世界书命中的设定原文、上章结尾、近期事件摘要、本章应回收的伏笔。
- prompt 里显式给出，并要求 `consistency` / `plot_logic` 的扣分**必须引用具体冲突点**。
- 证据缺失时（如 bootstrap 首章无前文），**明确告知模型"本章无前文可比对，consistency 按 setting 判"**，而不是沉默——沉默会让模型自由发挥。

**验收**：
- 证据出现在 prompt 里
- 故意造一个与设定冲突的章节，`consistency` 分数应显著低于无冲突版本（这是本项验收的核心，用 mock router 固定返回不行，需要设计成可断言的对比测试）
- 无证据时不崩、有降级提示
- token 预算不失控（证据也要有上限）

**风险**：checker 是 3 模型交叉调用，prompt 变长直接乘以 3 倍成本。证据预算要压紧。

#### A4. 校准闭环到 PASS_SCORE

**证据**：`orchestrator.py:67` 硬编码；`calibrate_checker.py:182` 结果无消费者（§2.3）。

**改法**：
1. 校准集从"内建人写/AI 写样本"扩展为**可外挂真实章节 + 人工标注分数**（`_load_samples()` 已支持外部样本，`calibrate_checker.py:101`，复用它）。
2. `run_calibration()` 输出增加 `recommended_pass_score`：由标注分数分布推导（例如取人工判定"可接受"与"不可接受"的分界）。
3. `PASS_SCORE` 改为读 `calibration_result.json`；**缺文件时用 6.5 默认值并打印明确告警**（"质量门未校准，使用默认阈值，判定结果仅供参考"）。

**验收**：
- 有校准文件时用校准值，无文件时用默认值 + 告警可见
- 校准文件损坏时不崩，降级到默认值 + 告警
- 阈值变化能真实改变 route 决策（`orchestrator.py:922/941`）

**风险**：阈值可配后，误配会让质量门形同虚设。建议加合理区间钳制并对越界告警。

#### A5. writer prompt 分层（**先测量，后动手**）

**证据**：24 个指令块（§2.4）。

**这一项与其它不同：不要凭直觉重构。** 先建立测量手段，否则改完无法判断是变好还是变坏。

**改法**：
1. 先做**遵循率测量**：构造若干条可自动检测的硬约束（如"本章必须出现角色 X""必须回收伏笔 Y""不得出现新角色名"），跑 N 章统计各约束的实际命中率。这可以用 mock 先验证测量代码本身，但**真实数据必须用真实 LLM**。
2. 拿到基线后再决定分层策略：候选方案是把 24 块压成"必须遵守（硬约束）/ 本章素材 / 风格参考"三段，硬约束靠近 prompt 末尾。
3. 改动后重跑同一测量，**用数据对比**。

**验收**：遵循率不下降，且关键约束（吞设定、伏笔回收）的命中率上升。

**风险**：这是最容易"改了感觉更好但实际更差"的一项。没有测量就不要改。

### Phase B：阶梯真实测试

严格按 **3 → 10 → 30 → 50** 推进。每一级修完问题再进下一级——不要一次跑 50 章然后面对一堆问题。

**前置**：
- 测试路径必须与前端使用逻辑一致（走 `/api` 而非直接调引擎）。走一遍前端的真实请求序列：创建项目 → 配 Provider → push-concept → planner → pull-setting → init_arc → run。
- 模型：优先 MiniMax（`MiniMax-M3` / `M2.7` / `M2.5`），额度不足换 DeepSeek（`deepseek-v4-flash` / `pro`）。key 在 `D:\Users\桌面\linshi.env`，**绝不打印值**。
- 先读 [07-Real-LLM-Testing](07-Real-LLM-Testing.md)：成本、限流、master_key 漂移、env 透传的坑都在那。
- MiniMax 的业务错误是 HTTP 200 + `base_resp.status_code`（2062 = 限流），已在 `engine/llm/router.py` 处理，跑之前确认仍然生效。
- 绑定目录**每次测试用独立目录**，不污染既有数据。

**每一级的检查项**（不是只看"跑完了"）：

| 级别 | 重点验证 |
|---|---|
| 3 章 | 链路通；章节标题正确；设定包字段完整；L2 有内容；预算记账准确 |
| 10 章 | 伏笔开始埋；人物状态累积；文风稳定；无跨项目专名泄漏 |
| 30 章 | **弧切换正确**；L5 弧归档生成；伏笔开始回收；热转冷压缩发生 |
| 50 章 | **一致性不衰减**；伏笔逾期数受控；设定回流提案合理；质量分不下滑 |

**成本控制**：`audit_mode` 用 `draft` 跑通链路（跳过 compliance + checker，省 ~70%），确认无误后再用 `full` 定稿关键章节。预算硬停在 state 里（`budget_limit_usd`）。

### Phase C：前端验证与优化

**验证方式**：截图 + 识图（图谱类必须看图，不能只看 DOM）、Playwright、或浏览器 MCP。

**必须逐项确认能在界面上看到且正确**：

- 大纲 / 细纲（`/projects/:id/outline`）
- 世界观 表世界+里世界（`WorldBuild` → 世界观 tab）
- 力量体系及等级（`WorldBuild:535`）
- 货币体系（`WorldBuild:596`）
- 地图节点层级（`WorldBuild:494`）——**截图验证层级树渲染**
- 角色卡（`/projects/:id/characters/:cid`）
- **角色关系图谱**（`RelationGraph.tsx`）——**必须截图识图**，确认节点/边真实渲染而非空画布
- **势力图谱**（`FactionGraph.tsx`）——同上
- 伏笔（`WorldBuild` 伏笔 tab + 记忆面板的逾期标记）
- 分层记忆（`MemoryPanel`，已接真实 API）
- 章节正文 + 章节标题（`ChapterReader`）
- 质量评分 / 预算 / BridgeRun SSE 日志（`BridgeConsole`）

**已知情况**：`npm run build` 通过（tsc + vite 均无错）。`data-testid` 目前只有新增的 `MemoryPanel` 有；其余页面需要 Playwright 时得靠 role/text 选择器，或按需补 testid。

**前端优化**（用户已明确加入目标，排在验证之后）：等验证暴露出真实痛点再做，不要提前臆想。

---

## 五、明确不做的事

写下来是为了防止后续跑偏：

- **不把引擎改成进程内调用**——进程隔离是长跑的保命设计。
- **不建平行系统**：复用现有 Schema、Provider 路由、角色分配、预算、质量门、分层记忆、BridgeRun。
- **不用向量相似度判事实性一致性**（人物年龄/势力归属这类）。这类问题交给结构化查询，向量检索在多跳事实推理上不可靠——`app/rag/retrieval.py:13-16` 的原注释已经说明了这个判断，它是对的，保留。
- **不做全量设定重生成**——见主张 3。
- **不新增依赖 / 环境变量 / 数据库表 / 公共 API**，除非任务明确需要（A1 的检索索引文件和 A2 的提案落盘要尽量复用既有目录结构与原子写入）。
- **不为了让测试通过而放宽断言**。发现断言锁定的是缺陷时，改成断言正确行为并写明原因。

---

## 六、已知遗留缺陷

不属于本路线图，但会咬人，记在这里：

- **`alembic upgrade head` 在干净库上失败**。`alembic/versions/0001_baseline.py:39` 的 `upgrade()` 是空的（设计上配合 `alembic stamp head`），但 `0003_*.py:60` 执行 `INSERT INTO projects_new SELECT * FROM projects` → `no such table: projects`。**影响全新部署**。行为测试里 `test_alembic.py::test_alembic_upgrade_head_on_clean_db` 长期红。
- 结构不变量测试 5 项长期失败（`test_bridge_run_pid`、`test_build` 的 MemorySaveAtomic、3× `test_schemas` SchemaValidatorFailFast）。修之前先确认它们锁的是真缺陷还是测试本身过时。
- `backend/backend/` 是残留目录（0 字节 db + 6 个 e2e 残留文件）。已通过 `tests/_paths.py` 的仓库根解析加固绕开，但目录本身还在。删除前先确认无人引用。

---

## 七、验证命令

行为测试与结构不变量测试**必须分两个 pytest 进程**：

```powershell
pytest backend/tests --ignore=backend/tests/invariants
pytest backend/tests/invariants
python -m compileall -q backend/app backend/engine backend/scripts backend/tests
npm --prefix frontend run build
git diff --check
```

当前基线（用于判断是否引入回归）：行为测试 **895 passed / 1 failed**（失败项为上述 alembic 缺陷）；`npm run build` 通过。

交付时必须报告实际运行的命令、结果、未验证项和剩余风险，并与改动前基线对照——**不得把既有失败说成新回归，也不得声称未实际运行的检查已通过**。
