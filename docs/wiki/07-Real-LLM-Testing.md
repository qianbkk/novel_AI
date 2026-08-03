# 07 · 真实 LLM 端到端测试 — 经验沉淀

> 本页保留真实长篇跑批仍然有效的操作约束、故障模式和质量验收方法。
> 历史运行日志与报告不是文档依赖：本地如需保留，统一放在已忽略的 `docs/runs/`；设计与修复过程通过 Git 历史追溯。

## 1. 跑真实 LLM 测试的最小流程

```bash
# 1. 启动后端和前端，在页面配置 Provider、角色绑定并创建项目
# 2. 完成世界构建；若已有 draft 项目中断，可续跑
cd backend
python -m scripts.continue_worldbuild <project_id>

# 3. 走与前端 BridgeConsole 等价的 HTTP + SSE 流程
python -m scripts.drive_30ch_bridge <project_id> --chapters 30
```

`drive_30ch_bridge.py` 为每个项目使用独立的 `backend/data/engine/project/<project_id>/`，不依赖日期目录或硬编码项目。运行日志由后端日志、`BridgeRun` 记录和项目专属引擎目录共同保存；如需额外实验输出，放入已忽略的 `docs/runs/<run-id>/`。

## 2. 30 章跑出来的 8 个问题

| # | 症状 | 根因 | 修复 |
|---|------|------|------|
| #1 | planner/bootstrap 报 `MINIMAX_API_KEY 未设置` | bridge subprocess 拿不到 env（父 uvicorn 没 .env） | `worker/.env` loader 注入（85dc898） |
| #2 | 重启 uvicorn 后 15 个 Provider 全解密失败 | dev master key 漂移 | fingerprint log + 漂移说明（6c8f660） |
| #3 | bootstrap SSE 在"生成版本C"后卡 1+ 分钟 | `_post_with_retry` 6 attempts 静默等待 | tenacity `before_sleep` 实时 print（6c8f660） |
| #4 | characters 表「林渊」重复 2 行 | stage_characters 没去重 | seen_names 集合去重（6c8f660） |
| #5 | 前端没看到大纲 | `worldbuild/result` 不返 arc_plans | `_load_arc_plans_from_engine` 辅助（6ffdb67） |
| #6 | 角色卡 8 段字段全 NULL | preset_worldbuild 强制走 mock | 默认走真 LLM（6c8f660） |
| #7 | 项目无创建/修改时间 | ProjectOut schema 缺字段 | +created_at/+updated_at（6ffdb67） |
| #8 | 章节标题是 `{"title": "..."}` JSON 字面量 | 5 处独立处理 title 都没解析 | 5 步全链路（6ffdb67） |

## 3. 5 步全链路修章节标题（问题 #8 深度复盘）

5 个独立处理 title 的代码点都"健壮地"把脏数据透传到下一步：

```
LLM 返 {title, body} 包装
  ↓
1. writer._extract_title
   └─ json.loads(text) 失败（body 含真换行符）
   └─ 降级第 4 级 _first_line_as_title(raw)
   └─ 拿到 raw 第一行 = "{"title": "...", "body":"
   └─ 截 30 字 → meta.title = JSON 字面量
  ↓
2. orchestrator.save_chapter
   └─ f.write(text) 整段落盘（含 JSON 包装）
  ↓
3. orchestrator.node_save_and_track
   └─ meta["title"] = task._draft_title  ← JSON 字面量
  ↓
4. chapter_import._derive_title
   └─ meta.title 优先 → 解析失败 fallback 仍把 JSON 字面量塞 DB
  ↓
5. 前端 ChapterReader.tsx
   └─ {chapter.title} 原样展示
```

修法：

| 步 | 文件 | 修 |
|---|------|-----|
| A | `writer._extract_title` | 加第 0 级手抽 LLM 半合法 JSON 包装 |
| B | `orchestrator.save_chapter` | 落盘前再调 `_extract_title` 剥 JSON |
| C | `orchestrator.node_save_and_track` | meta.title 写入前清洗（{开头 / 30+字走首句 fallback） |
| D | `chapter_import._derive_title` | 第 1 级 JSON 解析失败 → 走第 2 级，不让脏数据进 DB |
| E | `ChapterReader.tsx` | 前端 title 守卫（{开头 / 30+字返"（标题待生成）"） |

**本质教训**：5 处独立处理 = 没 single source of truth。每处都"健壮地"降级，但**降级路径没传"脏数据"信号**。修复本质 = 在最早环节（A 步）就把 JSON 解析干净，下游不用各自防御。

## 4. MiniMax-M3 真实 API 行为观察

- **响应慢**：单次 writer 调用 30-60s（vs mock < 1s）
- **HTTP 529（服务器过载）高频**：连续多章调用后 50% 概率返 529
  - 必须 `before_sleep` print 让用户看到 retry attempt=N/6
  - 6 attempts + 2-60s exp backoff 总等待 ~2 min 才能扛住
- **Token Plan 速率限制 (status_code 2062)**：30 章跑到 ~$1 时频繁触发
  - 30 章约 $1.07（**超原 $0.5 预算 2x**），后续要么升套餐要么 audit_mode=lite
- **LLM 不严格按 prompt 输出 JSON**：writer prompt 长（2200 字 + 设定）时 LLM 仍返 JSON 包装；短 prompt 不返
  - 修法：fallback 用 regex 手抽 title/body，绕过 json.loads

## 5. 日志详细度评估

按用户列举的 5 个失败场景，**现有日志能定位**：

| 失败场景 | 日志路径 | 粒度 |
|---------|---------|------|
| 世界观哪部分不完善 | `engine/output/setting_package.json` + `world_settings.world_view_rich_json` | 字段级 |
| 人物阵营没确立 | `worldbuild_snapshot.characters[]` + `L2.memory.hot.character_states` | 角色卡 8 段 + fuzzy-dedup 状态 |
| 伏笔没追踪好 | `L2.memory.constraints.foreshadowing_planted` + `cold.resolved_foreshadowing` + `foreshadowings` 表 | 章级 + 弧级 + 状态 |
| 地图/力量体系 | `setting_package.power_system.levels[]` + `MapNode` 表 + `world_view_rich.geography` | 等级名 + 节点坐标 |
| 物权追踪 | `L2.memory.hot.inventory[]` + 每章 `inventory_add/inventory_remove` | 道具级 + 章级 |

**4 层日志结构**（详细度从高到低）：

1. **`backend/logs/novel_ai.log`** — uvicorn 全量，5MB×5 滚动 25MB
2. **`docs/runs/<run-dir>/<cmd>_sse.jsonl`** — 每次 bridge.run 一份
3. **`BridgeRun` 表** — DB 持久化 stdout（兜底）
4. **engine 落盘产物** — `engine/output/chapters/` + `orchestrator_state.json` + `L2 memory`

## 6. 30 章性能基线

- **3-5 min/章**（含全 audit 3 checker + 0-3 次重写）
- **120 任务（4 弧 × 30 章）总耗时 ~2.5h**
- **预算：29 章 6.5+ 分真实通过** = $0.6-0.8 区间
- **30 章后大概率撞 Token Plan 速率限制**

## 7. 跑真实测试前的 Checklist

- [ ] `.env` 已设 `MINIMAX_API_KEY` / `MINIMAX_API_BASE`
- [ ] 后端是**用 .env env 启动**的（不是裸 uvicorn，env 不全会 5xx）
- [ ] `audit_mode=lite` 已 set（除非专门测 full 质量）
- [ ] `NovelAIBinding` 已设到本次 run dir（避免覆盖上次产物）
- [ ] `MiniMax Token Plan` 余额 ≥ $0.6
- [ ] 已有 MiniMax Provider + 15 role assignments 绑定（同进程防 master_key 漂移）
- [ ] budget_limit_usd ≥ $1（避免 BUDGET_HARD 硬停）

### 7.1 战略审视 + 审计修 后的必跑步骤

30 章跑完后,**除了之前 7 项 checklist,必跑**:

```bash
# 1. 节拍校验（扮猪吃虎 / 升级循环 / 情绪多样性 / 钩子存在）
python -m engine.tools.beat_checker backend/data/engine --window 10
# 期望:overall_status=GREEN(4 维度全绿)。有 RED 项需查 details 找是哪条规则
# 退出码:RED=2 / YELLOW=1 / GREEN=0

# 2. 12 项验收标准
python -m engine.tools.acceptance_tests all
# 期望:12/12 通过。无数据时全 SKIP → True

# 3. 单项 FAIL 排查
python -m engine.tools.acceptance_tests ac6   # 但是法则密度
python -m engine.tools.acceptance_tests ac7   # 信息差多样性
python -m engine.tools.acceptance_tests ac8   # 情绪多样性
python -m engine.tools.acceptance_tests ac9   # 三线分布
python -m engine.tools.acceptance_tests ac12  # 对话提示词密度
```

详细字段与规则见 [03-Writing-Engine.md § 方法论内化与节拍校验](03-Writing-Engine.md#方法论内化与节拍校验)。这些工具自 2026-07-25 起加入,跑 30+ 章后必跑。

### 7.2 战略审视 + 审计修 改了什么(2026-07-25~26)

每章自动携带的方法论/字段(无需手动配置):

- **4 招方法论内化**:writer prompt 默认注入信息差 / 但是法则 / 3 层期待感 / 模块化叙事
- **7 个 ChapterTask 字段**:outline 阶段自动填 `stakes` / `dilemma` / `narrative_thread` / `info_asymmetry` / `anchor_to` / `emotion_core` / `emotion_intensity`(老 task JSON 自动兼容)
- **normalizer 后处理**:满篇"某某说/道"对话癌检测(≥25 预警 / ≥50 强制 4 策略替换)+ POV 视角切换密度检测(默认 ≤2/章)
- **meta.json 字段完整**:审计修 Critical#1,save_chapter 落盘必含 7 字段,beat_checker / AC-10 / AC-11 不再恒为 YELLOW
- **dialogue_replace_prompt 安全**:审计修 Medium#1,污染样本含 `{` 不再 KeyError
- **card B/C 标准化**:审计修 Medium#4,选中 B/C 分支不再字段缺失 / 章号错乱

下次 30 章真跑前不需要手动设置任何方法论字段,流程照旧 → 跑完跑 §7.1 三条 CLI 即可。

## 8. 续跑卡住的 draft 项目（2026-07-24 实战）

真实模型世界构建若因 429 或解析失败中断，项目会留在 `draft` 状态。不要用一次性脚本另建项目；`backend/scripts/continue_worldbuild.py` 提供可复用的续跑路径：

- 接受现有 `project_id`，**不重建**
- 跑前先 `cleanup_partial` 把上一轮失败的 WorldSetting/Character 等中间产物清掉
- 跑 10 stages，每 stage 失败重试 6 次（429 / 5xx / JSON parse 全捕获）
- 跑完直接标 `project.status=ready` + 写 GenerationJob=done

具体耗时取决于 Provider 限流和模型响应速度；脚本保持同一项目语义并避免创建重复测试项目。

## 9. 30 章 Bridge pipeline 真实跑通（2026-07-24 实战）

`backend/scripts/drive_30ch_bridge.py` 跑通完整前端按钮等价流程：

```
binding → push-concept → planner → pull-setting → bootstrap → init_arc → run 30 → import-chapters
```

实测 31 章（3 章来自 bootstrap + 28 章来自 run 30）、$0.74 总成本、~40 分钟。

### 9.1 实测数据

| 阶段 | 耗时 | LLM 调用 | 备注 |
|------|------|---------|------|
| binding | <1s | 0 | 仅 PUT，N/A |
| push-concept | ~30s | 0 | 推 DB → engine/config/novel_config.json |
| planner | ~30s | 1 | planner agent 用 worldbuild_snapshot 降级为补全者 |
| pull-setting | <1s | 0 | engine output → DB WorldSetting/Character/... |
| bootstrap | ~7min | 9 | 3 章 × 3 candidates (A/B/C) |
| init_arc | <1s | 0 | setting.arc_outline → orchestrator.arc_plans |
| run 30 | ~30min | ~120 | 28 章真跑 + bootstrap 已写 3 章 = 31 章 |
| import-chapters | <2s | 0 | engine ch_NNNN.txt → DB Chapter |
| **合计** | **~40min** | ~130 | $0.74 |

### 9.2 实测发现的新问题

**问题 #13 — pull-setting 重复人物（本次发现）**
- planner 同时把 `protagonist.name` 放进 `key_characters[]`
- pull-setting 先 add protagonist 再 add key_characters → 同一 name 写 2 行
- 表现：本次跑出 7 个 character 含 2 个 `林渊`
- 修复（setting_sync.py）：`_add_character` 加 `seen_names` 守门，
  protagonist 先 add（更权威），key_characters 已见名字 skip
- 老数据用 SQL 一次性清理（31 个 chapter_characters 都指向被删的 duplicate → NULL 后重新指向 surviving 林渊）

**问题 #14 — Dashboard 看不到"正在跑"（本次发现）**
- `Project.status` 只有 draft/worldbuilding/ready 三态
- bridge.run 期间 status 不变 → 用户看不到"运行中"提示
- 修复：`/projects` endpoint 现在附带 `active_run_command/status/id/started_at`
  （每个项目最新一条 pending/running 的 BridgeRun）
- Dashboard 加 `runningBadge`：非空就显示⟳+命令名+呼吸动画，点了直接跳 BridgeConsole

## 10. 2026-08-03 真实 MiniMax 回归新增结论

- **writer JSON 长度预算必须按 `body` 计算**：首次真实生成目标 `2000-2200` 字时，旧实现把完整 `{title, body, title_alts}` 包装当正文，续写和截断边界错误，实际正文仅 1230 字。修复后长度预算先提取 `body`、续写只拼正文、最终再封装合法 JSON；最终复跑正文 2211 字，落在允许的 `1900-2300` 容差区间。
- **outline 硬约束必须进入 writer prompt**：`setting_constraints` / `forbidden_actions` 原先只存在 ChapterTask，writer 只读 memory 中的禁令，导致真实正文越过「不得直接读取尸内记忆」等约束。现已将两组字段作为显式硬契约注入 prompt，并加回归测试。
- **项目 checker 不能替代独立总编审评**：第一次长度修复复跑中，项目 checker 给 8.2，但独立九维总编因系统面板串题、禁令越界、人物底牌过早揭示给 6.2/FAIL。补上 outline 硬约束注入后最终复跑，项目 checker 8.95、独立总编 7.3/PASS_WITH_NOTE；仍指出功能性对白、闻痕级设定边界和妹妹线索过早揭底。真实验收应保留双评审，且 blocking issue 必须压过平均分。
- **Alembic 必须验证旧库升级，不只验证空库**：`0003` 在旧 SQLite 的匿名外键上无法按推测名称 drop，且 SQLite 不支持直接 `ALTER ADD UNIQUE`。现改为 batch copy-and-move，并覆盖 `0002 -> head` 旧库路径；实际项目数据库已升级到 `0003_fk_cascade_unique (head)`。
- **质量遗留**：本次章节长度已达标，但体裁污染与规划服从仍需通过重写/审核门禁继续治理，不能仅凭字数修复宣称内容质量达标。

## 11. 后续改进（待办）

- **DB 层加 `UNIQUE(project_id, name)` 唯一约束** 兜底 characters 重复
- **加 `GET /projects/<pid>/debug/state-snapshot` 聚合调试端点** 把 L2 + 角色 + 势力 + 伏笔 一次返回（用户提过需求，未做）
- **map_nodes 表写入 schema 修复**（当前 mock 阶段把弧名当节点）
- **foreshadowings 表写 desc 字段修复**（当前写盘时丢字段，worldbuild/result 有 desc 但表里是空）
- **chapter_import 整链路再加单元测试**（目前只测了 _extract_title，没测 _derive_title 的 fallback 链）
- **pull-setting 保留 stage_characters 的 card_*_json**：当前 stage_characters 写 8 段角色卡，
  pull-setting 删旧行后只写 detail_json，card 数据丢失（前端 /characters 列表 personality_summary 变空）
