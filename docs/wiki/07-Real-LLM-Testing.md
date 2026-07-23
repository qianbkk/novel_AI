# 07 · 真实 LLM 端到端测试 — 经验沉淀

> 本页沉淀 30 章真实 MiniMax-M3 端到端测试（2026-07-22 + 2026-07-24）发现的所有问题、根因、本质修复与教训。
> 每次跑真实 LLM 测试前先看本节 — 历史上踩过的坑不要再踩。
> 完整原始数据见 [`docs/runs/30ch-real-2026-07-22/REPORT.md`](../runs/30ch-real-2026-07-22/REPORT.md)（276 行报告）。
> 最新一次实战（2026-07-24，含 31 章真跑 + Dashboard 修复）见 [`docs/runs/30ch-real-2026-07-24/`](../runs/30ch-real-2026-07-24/)。
> 更早的 30 章跑（2026-07-20）已归档到 [`docs/runs/_archive/30ch-real-2026-07-20/`](../runs/_archive/30ch-real-2026-07-20/)。

## 1. 跑真实 LLM 测试的最小流程

```
1. cd backend && python scripts/preset_worldbuild.py
   → 创建 project + 用真 LLM 跑 10 阶段 worldbuild（不再走 mock）
   → 输出 project_id 写到 docs/runs/<run-dir>/pid.txt

2. python scripts/setup_minimax_provider.py
   → 创建 MiniMax-M3 Provider + 绑定 15 个 role assignment
   → 同进程必须（避免 master_key 漂移导致解密失败）

3. python scripts/test_30ch_phase2.py
   → push-concept → planner → pull-setting → bootstrap → select A →
     init_arc → run 30 → import-chapters
   → 详细 sse log 写到 docs/runs/<run-dir>/
```

每次跑用 `NOVEL_RUN_DIR` env 隔离目录（避免覆盖上次）。

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

## 8. 续跑卡住的 draft 项目（2026-07-24 实战）

`preset_worldbuild.py` 跑挂在 stage 2（MiniMax 突发 429 限流）后，
项目 `real30ch-16862056` 留在 `draft` 状态、绑定目录还没建。
本想整段重跑但 preset 总是**新建 project**，丢了"续跑"语义。

`backend/scripts/continue_worldbuild.py` 是这条修复路径：

- 接受现有 `project_id`，**不重建**
- 跑前先 `cleanup_partial` 把上一轮失败的 WorldSetting/Character 等中间产物清掉
- 跑 10 stages，每 stage 失败重试 6 次（429 / 5xx / JSON parse 全捕获）
- 跑完直接标 `project.status=ready` + 写 GenerationJob=done

该脚本只跑 ~14 分钟（10 stages × 60-90s），比 preset_worldbuild 短很多，
因为没有 mock fallback overhead，纯 LLM 链路。

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

## 10. 后续改进（待办）

- **DB 层加 `UNIQUE(project_id, name)` 唯一约束** 兜底 characters 重复
- **加 `GET /projects/<pid>/debug/state-snapshot` 聚合调试端点** 把 L2 + 角色 + 势力 + 伏笔 一次返回（用户提过需求，未做）
- **map_nodes 表写入 schema 修复**（当前 mock 阶段把弧名当节点）
- **foreshadowings 表写 desc 字段修复**（当前写盘时丢字段，worldbuild/result 有 desc 但表里是空）
- **chapter_import 整链路再加单元测试**（目前只测了 _extract_title，没测 _derive_title 的 fallback 链）
- **pull-setting 保留 stage_characters 的 card_*_json**：当前 stage_characters 写 8 段角色卡，
  pull-setting 删旧行后只写 detail_json，card 数据丢失（前端 /characters 列表 personality_summary 变空）
