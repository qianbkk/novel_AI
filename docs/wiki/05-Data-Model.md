# 数据模型（`backend/app/models.py`）

SQLite 存储，SQLAlchemy ORM。所有主键为 `gen_id()` 生成的 hex UUID 字符串。

## 实体关系概览

```
User (Phase4) ──┐
                 │ owner_id (nullable)
                 ▼
              Project ──1:1── WorldSetting
                 │
                 ├─1:N── Character ──1:N── ChapterCharacter ──N:1── Chapter
                 ├─1:N── Faction
                 ├─1:N── PowerSystem
                 ├─1:N── MapNode (自引用树, parent_id)
                 ├─1:N── Foreshadowing
                 ├─1:N── Currency
                 ├─1:N── EntityRelation (图边: from_type/from_id → to_type/to_id)
                 ├─1:N── Chapter ──1:N── EmbeddingChunk
                 ├─1:N── GenerationJob (世界构建向导进度)
                 ├─1:1── RuleConfig
                 ├─1:1── NovelAIBinding (→ 外部引擎工作目录)
                 └─1:N── BridgeRun (引擎子进程运行记录)

Provider ──1:N── RoleAssignment (15 个写作角色)
```

## 核心表

### `Project`

`genre`/`audience`/`config_json`/`status`（`draft|worldbuilding|ready`）/`ai_assist_level`/`budget_limit_usd`/`novel_ai_status`/`owner_id`（可空，Phase 3 多租户占位）/`audit_mode`（`full|lite|draft`，**项目级**而非全局环境变量）。

### `WorldSetting`

legacy 字段：`world_view`/`story_core`（纯文本）+ `plot_skeleton_json`/`special_settings_json`/`novel_ai_raw_setting_json`（引擎生成的原始 `setting_package.json`）；Phase 1 结构化字段：`world_view_rich_json`（7 大板块）、`story_core_struct_json`、`history_timeline_json`。

### `Character`

`name`/`role`/`detail_json` + Phase 1 八部分卡片列：`card_basic_json`、`card_appearance_json`、`card_personality_json`、`card_background_json`、`card_abilities_json`、`card_catchphrase_json`、`card_props_json`、`card_arc_json`。

### 其他世界设定表

- **`Faction`**：势力信息
- **`PowerSystem`**：`tiers_json`（力量体系分级）
- **`MapNode`**：自引用树（`parent_id`、`level`）
- **`Foreshadowing`**：`importance`/`status`/铺垫-回收章节提示
- **`Currency`**：货币体系
- **`EntityRelation`**：图边 `from_type/from_id → to_type/to_id`、`relation`，Phase 1 富化字段 `mutual`、`intensity`（0-10）、`tags_json`、`evolution_json`、`key_events_json`

### 章节相关

- **`Chapter`**：`chapter_no`/`title`/`content`/`summary`/`ai_assist_level`
- **`ChapterCharacter`**：章节↔角色出场关系边（RAG 过滤用）
- **`EmbeddingChunk`**：`source_type`（chapter/character/foreshadowing）/`source_id`/`text_snippet`/`embedding_json`/`model`

### Provider / 角色配置

- **`Provider`**：`provider_type`、`api_base`、`api_key_encrypted`（Fernet 密文，从不明文存储）、`api_key_suffix`（末 4 位）、`default_model`、`needs_proxy`
- **`RoleAssignment`**：`role_key`（唯一）→ `provider_id` + `model_override`，`ROLE_REGISTRY` 中每个角色一行（15 个角色）

### 引擎桥接相关

- **`BridgeRun`**：引擎每次调用一条记录，`command`、`args_json`、`status`（pending/running/done/failed）、`exit_code`、`stdout_text`、时间戳
- **`NovelAIBinding`**：`Project` ↔ `(novel_ai_dir, novel_id)` 1:1，指向外部引擎工作目录
- **`GenerationJob`**：世界构建向导进度追踪，`status`、`current_stage`、`progress_percent`、`consistency_warnings_json`

### 其他

- **`RuleConfig`**：每项目风格（webnovel/literary/wuxia）、`taboos_json`、`template`、`extra_json`
- **`User`**（Phase 4）：`email`（唯一）、`display_name`、`password_hash`（bcrypt）

## Schema 演进策略

日常 schema 变更走 `backend/app/migrations.py` 的幂等 `ALTER TABLE ADD COLUMN` 列表，随应用启动自动执行；`backend/alembic/` 只有 2 个版本（`0001_baseline.py` 标记当前 schema 为基线，`0002_phase4_users.py` 建 `users` 表），是给"显式版本化结构变更"（如 CI 场景）用的脚手架，并非日常开发流程。

## JSON Schema 数据契约

`backend/schema/` 下 5 个 Draft-7 JSON Schema 文件，由 `app/schema_validator.py` 在写入前校验：`setting_package.schema.json`、`chapter_meta.schema.json`、`world_view_rich.schema.json`、`character_card.schema.json`、`entity_relation_rich.schema.json`。存在原因：曾因引擎输出字段名与后端消费字段名漂移，导致相关表静默留空。
