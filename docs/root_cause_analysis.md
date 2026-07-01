# 5 类问题根因分析 + 体系性修复

> 写于 2026-07-01，覆盖「破境」50 章端到端生成后体检发现的全部问题。
> 目的不是"修 bug"，是"让 bug 的同类永远不再静默回归"。

---

## 总览

| # | 类别 | 表面现象 | 真实根因 | 体系性修复 |
|---|---|---|---|---|
| **A** | 数据契约缺失 | `pull_setting_package` 只解析 4/9 字段，世界构建 5 张表全空 | planner 和 consumer 没有共享 schema，字段名漂移 | `schema/setting_package.schema.json` + 双端 validate |
| **B** | 章节数据无 schema 约束 | 标题是"【修改后正文】"/"你他妈" | `meta.json` 是未文档化的隐式 schema，靠 prompt 强制 LLM | `schema/chapter_meta.schema.json` + validate |
| **C** | 跨表依赖顺序未保护 | 50 章 0 个 ChapterCharacter 边 | import_chapters 和 pull_setting_package 都有"先 worldbuild"的**口头约定**，代码里没强制 | `audit_project` 跑一遍就暴露 + Project.worldbuild_state 字段 |
| **D** | schema vs ORM 不一致 | `ChapterFull.created_at: datetime` 必填，50 章 NULL → 500 | Pydantic schema 是"API 想要"的视图，ORM 是"DB 怎么存"的真相，两边 nullable 漂移 | `tests/test_invariants.py` 自动对比 |
| **E** | 写入端无校验 | ch1/ch42/ch50/ch7/ch21/ch32 首行是占位/卷首/markdown heading，渗到 preview | 写入 agent 没有"首行必须是正文"的不变量 | `_derive_title` 5 个 junk pattern + audit E 检查 |

---

## 根因 1：数据契约缺失（A 类）

### 现象
`pull_setting_package` 重写后，5 张表仍然全空：`WorldSetting.world_view=0 字`，`Faction=0`，`MapNode=0`，`Foreshadowing=0`，`EntityRelation=0`。

### 真正原因
继承老 `novel_AI` 时只看了 `plot_skeleton_json`（这是 `engine/agents/planner.py` 唯一生成的字段），**没去看 planner 实际在 `setting_package.json` 里写了哪些字段**。`KNOWN_CHARACTER_KEYS` / `KNOWN_POWER_KEYS` 两个白名单里写的 `characters/main_characters` 是老 novel_AI 的字段名，但新版 planner 实际写的是 `key_characters` / `power_system`——名字漂移。

更根本的：**项目有 3 个"真相"分散在 3 处**——

1. `engine/agents/planner.py` 写的 JSON schema（生产者）
2. `novel_AI/agents/planner.py` 老 `run.py` 用的字段名（历史）
3. `app/bridge/setting_sync.py` 的 `pull_setting_package`（消费方）

三者之间没有"单一真相源"，也**没有 schema 文件**。

### 体系性修复
1. **写 `schema/setting_package.schema.json`** — JSON Schema 草案，生产 / 消费双方都遵守
2. **`app/schema_validator.py`** — 单例加载 + 校验
3. **planner.py 写盘前 validate** — `jsonschema.Draft7Validator.iter_errors()`，fail-fast
4. **setting_sync.pull_setting_package 读盘后 validate** — 防止手工改文件绕过 planner 端校验
5. **`tests/test_invariants.py::TestSettingPackageSchema`** — 锁住 schema 必要字段

### 未来加字段的正确流程
1. 改 `schema/setting_package.schema.json`（加 required / properties）
2. 改 `engine/agents/planner.py` 的 PLANNER_SYSTEM prompt（加字段说明）
3. 改 `app/bridge/setting_sync.py::pull_setting_package`（加解析逻辑）
4. 跑 `python -m scripts.audit_project` 验证
5. 跑 `python -m pytest tests/test_invariants.py -v` 验证

任何一步漏掉都会被 audit / test 抓出来。

---

## 根因 2：章节数据无 schema 约束（B 类）

### 现象
- ch1 标题 = "【修改后正文】"（冒烟测试残留）
- ch1 summary = "你他妈"（4 字脏话占位）
- ch50 标题 = "第50章 万族共主"（与首行重复）

### 真正原因
`meta.json` 是**未文档化的隐式 schema**。`planner.py` 的 prompt 里写"应该有哪些字段"，但没有"强制约束"——LLM 想写就写、不想写就空。下游 5 个文件（`import_chapters.py` / `_derive_title` / `bridge.py` schema / `ChapterFull` Pydantic）各取所需、各自假设、各自漏。

### 体系性修复
1. **`schema/chapter_meta.schema.json`** — 锁住 meta 必有字段（chapter_number / chapter_role / chapter_goal / score / word_count）
2. **`import_chapters.py` 校验 meta** — 缺字段就 warn + 用兜底，不允许静默漏
3. **`_derive_title()` 跳过 5 种 junk pattern**（之前只跳 1 种）：
   - 空行
   - 纯 scene label `【xxx】`（≤30 字）
   - `第N章 标题` 重复
   - `【卷名】第N章 标题` 复合（ch42 bug）
   - `# 第七章 xxx` markdown 标题（ch7 bug）
   - `---` 分隔线（ch21/32 bug）
4. **`_build_summary()` 永不返回空** — 缺 chapter_goal 时：
   - 优先 status=human_required → "本章评分未达标，需人工补全"
   - 兜底用正文首句
   - 全空 → "本章 N 字"

---

## 根因 3：跨表依赖顺序未保护（C 类）

### 现象
50 章 0 个 ChapterCharacter 边。"按角色搜章节" 返回 0 结果。

### 真正原因
`import_chapters` 和 `pull_setting_package` 都有"先 worldbuild 再 import" 的**口头约定**，但代码里没强制。`BridgeRun` 表只记 `status='running'` 防重入，没记"运行步骤"。当我从老 `novel_AI` 拉 50 章过来时，**import 早于 pull**——于是 `add_chapter` 找不到任何 character 可建边。

### 体系性修复
1. **`scripts/audit_project.py`** — 跑一遍就把 50 章里有 0 character 边、3 伏笔无 character 关联、4 章节 NULL summary 等一次性暴露
2. **`tests/test_invariants.py::TestPydanticNullable`** — schema nullable vs ORM nullable 不一致自动比对
3. **`add_chapter` 写入"0 edges" 日志** — 不再 silently 跳过，未来 import 早于 pull 会明显报 WARN
4. **未来方向**（未实施，等下次重构）：`Project.worldbuild_state` 字段，import_chapters 入口处 `if state != 'done': raise 400`

---

## 根因 4：schema 与 ORM 模型分离（D 类）

### 现象
`GET /chapters/{id}` 返回 500，错误：`Input should be a valid datetime [type=datetime_type, input_value=None]`。50 章全 NULL created_at 全部 500。

### 真正原因
Pydantic schema 是"API 想要什么"的视图，ORM model 是"DB 怎么存"的真相。两者**应该**保持 nullable 一致，但 ORM `Chapter.created_at` 是 `default=datetime.utcnow`（实际写入时会填）— 但 `_force_reimport` 路径绕过了 `add_chapter` 的 default hook，直接 NULL 落库。Pydantic 那边写的 `created_at: datetime`（必填）就 500。

### 体系性修复
1. **`ChapterFull.created_at` 改 `Optional[datetime]`**（已修）
2. **`tests/test_invariants.py::TestPydanticNullable`** — 锁住"schema nullable 必须 >= ORM nullable"
3. **`_force_reimport` 路径**：NULL created_at 自动回填 `datetime.utcnow()`

---

## 根因 5：写入端无校验（E 类）

### 现象
3 个章节 txt 首行是"假标题"（【修改后正文】/ 【玄幻·人族秘史卷】第42章 父债子偿 / 第50章 万族共主），渗到 API preview。

### 真正原因
**写入端没有"首行必须是正文"的不变量**。Writer agent 可能因模板残留 / LLM 幻觉 / 测试 fixture，写出"以标题起头"的章节。`import_chapters` 只看 meta，不看 content 自身。

### 体系性修复
1. **`_derive_title()` 5 个 junk pattern**（见根因 2）
2. **`scripts/audit_project.py` E 检查** — 50 章 txt 首行扫描，发现问题就 WARN
3. **未来方向**（未实施）：写入端加 `assert content[:200] 不匹配 junk pattern`，写入时 fail-fast

---

## 体系性防御如何运作

### 日常开发
```bash
# 1. 改完代码后跑这个，至少 31/32 PASS 才算改完
python -m scripts.audit_project

# 2. 跑单元测试锁住不变量
python -m pytest tests/test_invariants.py -v
# 应该 19/19 PASS
```

### 加新字段时
1. 改 `backend/schema/setting_package.schema.json`
2. 改 `engine/agents/planner.py` 的 PLANNER_SYSTEM
3. 改 `app/bridge/setting_sync.py` 的解析逻辑
4. 跑 audit → 应该新增一个 "X8: 新字段已 spec" PASS
5. 跑 pytest → 应该新增一个 `test_X_field_required` PASS

### 加新章节源时
1. 让新源也走 `import_chapters_from_novel_ai()` 入口（统一继承 meta 校验 + 标题派生 + summary 兜底）
2. 不允许直接在 `add_chapter()` 写入，绕过 `_derive_title`

### Pre-commit / CI
- 把 `python -m scripts.audit_project --strict` 和 `pytest tests/test_invariants.py` 加进 CI
- 任何 PR 必须 32/32 PASS + 19/19 PASS

---

## 文件清单

### 新增
- `backend/schema/setting_package.schema.json` — planner ↔ consumer 契约
- `backend/schema/chapter_meta.schema.json` — meta.json 契约
- `backend/app/schema_validator.py` — 校验器（双端使用）
- `backend/scripts/audit_project.py` — 端到端不变量审计
- `backend/tests/test_invariants.py` — 锁死测试（19 个）
- `backend/scripts/fixup_50ch_audit.py` — 一次性修复 50 章边 / summary
- `backend/scripts/strip_chapter_headers.py` — 一次性 strip 假标题
- `docs/root_cause_analysis.md` — 本文档

### 修改
- `backend/engine/agents/planner.py` — 写盘前 validate
- `backend/app/bridge/setting_sync.py` — 读盘后 validate
- `backend/app/bridge/chapter_import.py` — `_derive_title` 5 个 junk pattern + `_build_summary` 永不空
- `backend/app/schemas.py` — `ChapterFull.created_at` 改 Optional

---

## 一句话总结

> **之前 5 类 bug 的共同根因 = 跨文件/跨表/跨进程的不变量没有写进代码。修复方法 = 把不变量变成 schema、变成 test、变成 audit 脚本，让它们在 CI 里自己抓问题。**
