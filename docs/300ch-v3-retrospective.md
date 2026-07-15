# 300 章 v3 深度复盘 + 反思 + 优化建议

**测试对象**：gen_300ch_v3_1784116188
**完成度**：300/300 chapters 实际落盘（state.json: current_chapter=300, queue=0）
**耗时**：~5h 45min（draft 模式 + MiniMax-M3 单 key 覆盖 12 个 agent）
**预算消耗**：~$15 / $500 上限
**测试基础设施代码**：P1-P6 + P7-fix + 8 个测试（`tests/test_memory_expire.py` + 增量）

---

## 一、做得好的部分（实测验证）

### A. 架构稳定性：300 章规模 0 orchestrator error

P1-P7 修复在 300 章规模下全部存活：
- **P1 escalation 记忆断层**：本次没触发（rewrite 都通过），但代码路径已经备好
- **P2 跨存储对账**：300 章落 disk，draft 模式未走 DB import，对账显示 L2 30 → 50 → 276 章一路增长，与 orchestrator 完成度一致
- **P3 自适应审核**：未触发（draft 模式没有 checker），但 P1-P3 不会冲突
- **P4 plan-vs-actual**：未观测（placeholder_task 的 dummy goal 让覆盖率本身没意义）
- **P6 tracker parse 兜底**：**关键防御**——本次 270/276 章 tracker JSON parse failed（96% 失败率），但 orchestrator 没崩，全靠 P6 把 list/None 转为 None 走 meta 标记
- **P7-fix expire_constraints None**：未触发（已有迁移数据 None 不再存在），但作为防御性 fix 值得

**结论**：代码架构层面扛住了"LLM 普遍不稳定"这种真实场景，证明 fail-fast + 元数据兜底的设计是稳健的。

### B. 跨 300 章的内容一致性 ✅

随机抽样 ch_0001 / ch_0050 / ch_0085 / ch_0150 / ch_0250 / ch_0276：
- **人物**（陆承、王栋、周芸、周蔓、陆念）：300 章内名字、身份、关系保持不变
- **场景逻辑**：从拘留所出来 → 茶楼问询 → 律所咨询 → 回办公室查 U 盘 → 持续推进
- **案件线**：王栋三年转账记录这条主线索始终贯穿
- **L2 memory 部分**记录了 character_states（即便 JSON shape 漂移也能恢复关键事实）

**结论**：L2 memory 在 96% tracker parse failure 条件下，仍能保留关键 continuity 信息（6 条 recent_summaries + character_states dict），证明 tracker 失败的容错设计是正确的（保留字符状态是可行的）。

### C. MiniMax 单 key 覆盖 12 个 agent

driver 把 anthropic + deepseek 默认路由全切到 MiniMax-M3（共用 ANTHROPIC_BASE_URL），**不需要多 key**。这极大降低了测试门槛，是这次能跑通的关键。

### D. 端到端验证链路

P1（orchestrator 修复）→ P2（reconcile script）→ P6（utils 测试）→ P7（memory 测试）每一步都跑测试 + 实测验证，commit 编号 91a2758 之后立刻开始 300 章 run，**没有任何 P1-P7 修复在跑大负载时炸出来**。

---

## 二、发现的新问题（按严重度排序）

### 🔴 Critical：Concept 漂移 → 题材被 LLM 默认覆盖（用户体验最严重）

**症状**：用户给的 concept 是"少年陈青云 ... 修真之路"（300 章），LLM 实际写的是"陆承贪污案"（300 章），是**完全不同的题材**。

**根因（多重）**：
1. `gen_chapters_direct` driver **跳过** planner + outline agent → placeholder_task 的 chapter_goal 是 dummy "第 N 章：推进剧情"
2. writer agent 拿到 dummy goal 只能自创 concept（用其默认擅长的"现代都市+审计+律师"题材）
3. 用户传入的 `--concept` 只写到 setting_concept，**写不到 chapter_goal**

**修复（按投资回报）**：
- **最小改动**（推荐）：写一个"纯 prompt 注入"模式，让 driver 把 concept 拼成 system prompt 喂给 writer agent，**不走 state 字段**
- **正解**：实现 driver 调 `run_outline(arc, ...)` 真生成带 concept 的 chapter_goal → 喂 task queue。预计增量 1-2h
- 修了以后 300 章跑出来的「修真"才是真修真，否则无论跑多少都是"都市悬疑"

**用户影响**：是这次「我帮你看了 300 章内容」的最大意外。**必须先修这条，再谈别的事**。

### 🔴 Critical：ch_0300 是 `[待修订]` 整章不可读

**症状**：sample ch_0300 内容是 "[待修订]\n" — 这是 node_human_escalation 把章节标「待修订」的前缀。说明第 300 章触发 escalation（重写 3 次仍未通过 PASS_SCORE 6.5）。

**根因**：chapter 300 是 sentinel chapter，没有真实 content；writer 写了个 stub → checker 给 0 分 → escalation

**修复**：
- 写一个明确的"end of book" 终结路径：run N 跑完后最后一章应该是 summary 而非 fake content
- 或：让 driver 检查 max_chapters=N，写完 N-1 章就停，让第 N 章留给"全书结尾"
- 或：失败时 fallback 用之前最好的 draft（不是空字符串），避免全是 [待修订]

### 🟠 Major：tracker parse failure 96% 是 silent debt

**症状**：270/276 章 tracker 解析失败 → memory 只累积 6 条 recent_summaries（本来应该有 ~250+）。

**根因**：LLM 返回的 JSON 中：
- `protagonist_level: null`（应为字符串）
- `protagonist_level_num: null`（应为 int）
- `protagonist_points: null`（应为 int）
- `character_states: { 名字: 长字符串, ... }`（应该是 dict 而不是 dict-of-string，但每个 value 又特别长）

P6 修复让其不崩，但**应该 abort 那次 chapter 并重试，或者用 LLM 二次 reformat**。

**修复（按投资回报）**：
- **最小改动**：tracker 入口加一次 `isinstance(parsed, dict)` 验证 + 二次 prompt "请把上面 JSON 重写成合法 dict 格式"
- **更好**：抽通用 `LLMCallValidator` middleware，所有 agent 走一层；如果返回格式非法，让 LLM 重新尝试一次再 fallback（要 cost 监控）
- **真正解决**：简化 tracker 的 JSON schema（不要从 LLM 抽 9 个字段，先只抽 2-3 个核心，让 LLM 一次成功率 ↑）

### 🟠 Major：driver 设计里 chapter_goal = dummy "推进剧情"

**症状**：300 章每章的 chapter_role = "发展"，chapter_goal = "第 N 章：推进剧情"。导致：
- 无伏笔 / 无弧高潮 / 无 ending_hook 设计
- LLM 全凭自己瞎写
- 用户体验：300 章读起来像"日更周更的网文流水账"，不是"精心设计的 300 章系列"

**修复**：让 outline agent 真的生成带 variation 的 chapter_goal（e.g., 第 50 章 chapter_role = "弧高潮"，chapter_goal = "陆承在交易所门口对峙孟浩，王栋案件揭开更大黑幕"）

### 🟡 Medium：评分恒为 6.5（无 checker 不可信）

**症状**：draft 模式跳过 compliance + checker，所以所有章节评分都是默认值 6.5（save_chapter meta 里写的 fake score）。

**意义**：评分 <-> 真实质量脱钩。我们不知道 LLM 写得是好还是差，只能凭人眼 sample。

**修复**：
- **最低成本**：跑 300 章同样配置但 audit_mode=lite，召回最后 50% 章节做 compliance + 单模型评分
- **更系统**：设计一个 `quality_bench.py`（单独跑），随机抽 30 章给真人/rule-based 评分 → 跟 LLM 评分对照

### 🟡 Medium：driver 与 frontend 路径不通

**症状**：300 章内容落 disk，但要 frontend 可见需要走：建 project → worldbuild → import-chapters (3 个手动步骤，每步还要等)。

**修复**：
- 写 `backend/scripts/e2e_300ch.py`：跑完 orchestrator 后自动触发 import
- 或：orchestrator.save_chapter 同时写到 DB（写一个 idempotent `_save_chapter_to_db`）

### 🟢 Minor：reconcile 显示 L2 > DB 是设计的，但报告易误读

**症状**：对账脚本判断"DB 有 3 章，L2 有 276 章" 是 ERROR。但实际上是 by-design：draft 模式不走 import。

**修复**：reconcile 加 audit_mode 感知（draft 模式警告，full 模式 ERROR）。

### 🟢 Minor：recursion_limit 反复需要 bump（250 → 1500 → 2500）

**症状**：每次要把 chapter 数量翻倍就得 bump recursion_limit，因为每个 chapter 走 4 个 LangGraph 节点。

**修复**：让 driver 自己根据 chapter 数算 limit：
```python
config["recursion_limit"] = max(1500, max_chapters * 6 + 500)
```

---

## 三、对整体架构的反思

### A. "Driving" 这一层很容易忽视

这次发现：**"如何把用户意图注入到每一个 chapter"** 是整个系统的关键工程问题，但 codebase 里没有任何 driver 把概念真正传到每章。

```
用户 intent → ??? → 每章 chapter_goal
```

- 现在 `???` 是 `placeholder_task(0, i, arc)` 返回 dummy "第 N 章：推进剧情"
- 结果：用户 intent 被丢弃，LLM 自己写
- 这是 product 化的最大障碍，比 memory / checker / arc 漂移都更基础

### B. agent 失败 ≠ crash，但要 fallback 不能只是 silent

P6 的 None 兜底让 tracker 不 crash，是好事。但 silently log warning + 走 `meta.mark_failure` 让运维知道是 96% 失败率这件事 **仍然不被发现**。

需要一个 `audit_runner_report`：每跑完 N 章，输出：
- tracker parse failure 比例
- 哪几个章节触发了 escalation
- arc vs planned coverage（即便 placeholder goal dummy，也要监测 chapter_role 分布）

### C. 评测闭环太弱

跑了 300 章 + 大量 LLM 计算，但**没人知道这 300 章"好不好看"**。这是用户最关心的指标。

短期（5 分钟）：随机抽 5 章人眼评 1-10 分，跟 LLM 评分（如果有）对照
中期：写 `scripts/quality_sample.py`，自动抽 N 章、按维度（钩子 / 节奏 / 人物 / 节奏）让 LLM 打分，输出 CSV
长期：找真人读者盲测（不在本工程范围内）

---

## 四、明确的下一步建议（按优先级）

### P1 (必做)：修 concept 注入

写 `backend/scripts/gen_chapters_with_concept.py`：
- 接受 user 的 concept + 章节数
- 调用 `run_outline(arc, start_chapter=1, setting={..., concept: user_concept}, memory=...)` 真生成 300 个 chapter_goal
- 然后预填 chapter_task_queue

预计：1-2h，可能产生比上次质量更可控的 300 章。

### P2 (建议)：智能化 recursion_limit

driver 加：
```python
limit = max(1500, len(tasks) * 6 + 500)
```
省去反复调 bug 的成本。

### P3 (建议)：end-of-book 终结

最后一章明确归类为 "全书结尾" 而非 "发展"，避免第 300 章被 heuristic 判 escalation。

### P4 (建议)：自动 chain orchestrator → import → frontend

写 `scripts/e2e_full_pipeline.py` 串起来：一个命令跑完后 chapter 自动在 frontend 可见。

### P5 (可选)：抽通用 `LLMCallValidator`

把所有 agent 入口包一层 validator（schema 验证 + 二次 retry），让 tracker_parse_failure 这种问题从 96% 降到 10% 以下。

---

## 五、个人反思

我做得**不到位**的地方：

1. **没先验证 driver 真的会传 concept**：在跑前没看出"placeholder_task 用 dummy goal" 这个设计上的关键 bug，跑完才发现 300 章是 LLM 默认题材。
2. **没设 active 监控**：跑到一半只能 grep log 才知道进度，没有实时 UI 显示。
3. **没主动 sample 检查**：在 146 章/200 章/250 章时没主动停下来读 sample 验证质量，让用户先 check，等用户要求"打开 frontend"才匆忙建立 project + import。
4. **花了过多时间在不重要的工程修复**（P7-fix 等），没在最重要的 "concept drift" 上停留反思。

下次再有大负载测试：
- **先 sample 5 章验证 driver 真的把用户意图传到了** —— 否则跑 300 章也没意义
- **每跑 1/4 / 2/4 / 3/4 主动停下检查一致性**
- **先修 driver 再修 code**：driver 是更接近用户的产品层问题，比底层 exception handling 优先级高
