"""shared.setting_models — 核心链路 Pydantic 模型（修 P0 短板裸 dict）。

2026-07-25 新增（修 P0-6 短板）。

之前 setting_sync.py 30+ 处 `dict.get("xxx")`、`raw.get(...)`，字段名漂移
只能在运行时（消费端看不到字段 → 5 张表全空）才发现。修了 jsonschema 校验
后只挡了 LLM 输出边界；Python 端仍然是无类型裸 dict。

修法：用 Pydantic v2 镜像 backend/schema/*.json 的核心结构，作为 setting
链路（pull_setting_package / chapter_import / outline）的强类型契约。

设计原则：
  - 只 mirror 关键路径需要的字段（不全字段照抄 JSON schema——避免重复维护）
  - 字段用 default=None + 严格类型，让下游代码用 model.xxx 代替 dict.get
  - 用 ConfigDict(extra='allow') 保留 schema 的 additionalProperties
    兼容（LLM 可能加新字段，不让 strict 校验拦）
  - additionalProperties=true 的 schema 字段类型用 dict[str, Any] 兜底

只覆盖 4 个最关键模型：
  - SettingPackage: pull_setting_package 入口（planner 输出）
  - WorldviewRich: stage_world_basics 输出（7 段世界观）
  - CharacterCard: stage_characters 输出（8 段角色卡）
  - EntityRelationRich: stage_relations 输出（富关系）

完整 schema 仍在 backend/schema/*.json，本模块不替代。
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class _BaseModel(BaseModel):
    """统一配置：保留额外字段（LLM 偶尔加新字段不应被 strict 校验拦）。

    ConfigDict(extra='allow') 等价 pydantic v1 的 class Config extra = 'allow'。
    """
    model_config = ConfigDict(extra="allow")


class ProtagonistCard(_BaseModel):
    """主角卡。"""
    name: str = ""
    age: int | None = None
    background: str = ""
    personality: str = ""
    speech_quirks: list[str] = Field(default_factory=list)
    awakening_trigger: str = ""
    initial_power_level: str = ""


class WorldSetting(_BaseModel):
    """world_setting 块（surface/hidden + unique_elements）。"""
    hidden_world_name: str = ""
    hidden_world_history: str = ""
    surface_world_name: str = ""
    unique_elements: list[str] = Field(default_factory=list)


class PowerSystem(_BaseModel):
    """power_system 块（name + currency + description + levels[]）。"""
    name: str = ""
    currency: str = ""
    description: str = ""
    levels: list[dict[str, Any]] = Field(default_factory=list)


class KeyCharacter(_BaseModel):
    """key_characters 数组项。"""
    name: str
    role: str | None = None
    speech_quirks: list[str] = Field(default_factory=list)
    background: str | None = None
    # 扩展字段（LLM 可能加 personality / abilities 等）
    # extra='allow' 自动接受


class ArcOutlineItem(_BaseModel):
    """arc_outline 数组项（单卷/单弧）。"""
    arc_id: int
    arc_name: str = ""
    arc_goal: str | None = None
    estimated_chapters: int | None = None
    arc_climax_description: str | None = None
    arc_climax_chapter_offset: int | None = None
    emotion_curve: str | None = None
    new_characters_introduced: list[str] = Field(default_factory=list)
    arc_ending_state: str | None = None
    is_final_arc: bool = False


class ForeshadowingSeed(_BaseModel):
    """foreshadowing_seeds 数组项。"""
    content: str = ""
    target_arc: int | None = None
    linked_character: str | None = None
    importance: str | None = None  # "high" / "medium" / "low"


class GoldenChapterHooks(_BaseModel):
    """golden_chapter_hooks 块。"""
    chapter_1_opening: str | None = None
    chapter_1_shuang_point: str | None = None
    chapter_3_cliffhanger: str | None = None


class SettingPackage(_BaseModel):
    """planner 输出顶层契约。

    pull_setting_package 入口先 model_validate(raw) — 失败抛 ValidationError，
    在 setting_sync 层翻译成 SchemaError 风格的报错。消费端下游代码用
    `pkg.world_setting.hidden_world_name` 替代 `raw["world_setting"]["hidden_world_name"]`，
    IDE 能给类型提示 + 字段写错立刻红线。

    字段命名 1:1 对应 backend/schema/setting_package.schema.json。
    """
    novel_id: str
    platform: str = "fanqie"
    genre: str
    budget_limit_usd: float | None = 500.0
    title_candidates: list[str] = Field(default_factory=list)
    tagline: str = ""
    protagonist: ProtagonistCard
    world_setting: WorldSetting
    power_system: PowerSystem = Field(default_factory=PowerSystem)
    key_characters: list[KeyCharacter] = Field(default_factory=list)
    arc_outline: list[ArcOutlineItem] = Field(default_factory=list)
    foreshadowing_seeds: list[ForeshadowingSeed] = Field(default_factory=list)
    golden_chapter_hooks: GoldenChapterHooks | None = None
    # LLM 偶尔加的扩展字段（e.g. taglines, themes）走 extra='allow'


# ─── 世界构建板块 3 个模型（用于 worldbuild stages）──

class WorldviewRich(_BaseModel):
    """stage_world_basics 输出（7 段世界观）。

    7 段：cosmos, geography, history, society, technology, races, customs。
    每段 ≥ 60 字（backend/app/worldbuild/stages.py:WORLD_BASICS_SYSTEM 规定）。
    """
    cosmos: str = ""
    geography: str = ""
    history: str = ""
    society: str = ""
    technology: str = ""
    races: str = ""
    customs: str = ""


class CharacterCard(_BaseModel):
    """stage_characters 输出（角色卡 8 段）。"""
    basic: dict[str, Any] = Field(default_factory=dict)
    appearance: dict[str, Any] = Field(default_factory=dict)
    personality: dict[str, Any] = Field(default_factory=dict)
    background: dict[str, Any] = Field(default_factory=dict)
    abilities: dict[str, Any] = Field(default_factory=dict)
    catchphrase: dict[str, Any] = Field(default_factory=dict)
    props: dict[str, Any] = Field(default_factory=dict)
    arc: dict[str, Any] = Field(default_factory=dict)


class EntityRelationRich(_BaseModel):
    """stage_relations 输出（富关系）。"""
    mutual: bool = False
    intensity: int = 5
    tags: list[str] = Field(default_factory=list)
    evolution: list[dict[str, Any]] = Field(default_factory=list)
    key_events: list[dict[str, Any]] = Field(default_factory=list)


__all__ = [
    "SettingPackage",
    "ProtagonistCard",
    "WorldSetting",
    "PowerSystem",
    "KeyCharacter",
    "ArcOutlineItem",
    "ForeshadowingSeed",
    "GoldenChapterHooks",
    "WorldviewRich",
    "CharacterCard",
    "EntityRelationRich",
]
