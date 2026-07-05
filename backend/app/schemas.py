from datetime import datetime
from typing import Optional, Any
from pydantic import BaseModel, ConfigDict


class ProjectCreate(BaseModel):
    title: Optional[str] = None
    genre: str
    audience: Optional[str] = "男频·青年向"
    config_json: dict[str, Any] = {}


class ProjectOut(BaseModel):
    id: str
    title: Optional[str]
    genre: str
    audience: Optional[str]
    status: str
    budget_limit_usd: Optional[float] = None
    novel_ai_status: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)


class JobOut(BaseModel):
    id: str
    project_id: str
    status: str
    current_stage: Optional[str]
    progress_percent: int

    model_config = ConfigDict(from_attributes=True)


class ChapterCreate(BaseModel):
    chapter_no: int
    title: Optional[str] = None
    content: str


class ProviderCreate(BaseModel):
    """前端 POST/PUT 时传 api_key 明文（只在传输过程中明文）。
    后端写库前用 security.encrypt_api_key 加密。
    """
    name: str
    provider_type: str
    api_base: Optional[str] = None
    api_key: str
    default_model: str
    extra_json: Optional[dict[str, Any]] = None
    needs_proxy: bool = False


class ProviderOut(BaseModel):
    """API 返回：绝不返回明文 api_key，只返回后 4 位 + 是否设置标记。"""
    id: str
    name: str
    provider_type: str
    api_base: Optional[str] = None
    default_model: str
    extra_json: Optional[dict[str, Any]] = None
    needs_proxy: bool = False
    api_key_suffix: Optional[str] = None  # 后 4 位，"sk-...xxxx"
    api_key_set: bool = False            # True = 已配置（用户能看到 suffix），False = 未配置
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class RoleAssignmentOut(BaseModel):
    model_config = ConfigDict(protected_namespaces=())

    role_key: str
    label: str
    provider_id: Optional[str] = None
    provider_name: Optional[str] = None
    provider_type: Optional[str] = None
    model_override: Optional[str] = None


class RoleAssignmentUpdate(BaseModel):
    model_config = ConfigDict(protected_namespaces=())

    provider_id: Optional[str] = None
    model_override: Optional[str] = None


class BridgeRunRequest(BaseModel):
    command: str
    args: list[str] = []
    outline_mode: Optional[str] = None  # batch | card | talk


class BridgeRunOut(BaseModel):
    id: str
    project_id: str
    command: str
    args_json: Optional[dict[str, Any] | list[Any]] = None
    status: str
    exit_code: Optional[int] = None
    stdout_text: Optional[str] = None
    started_at: datetime
    finished_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


class NovelAIBindingUpsert(BaseModel):
    novel_ai_dir: str
    novel_id: Optional[str] = None


class NovelAIBindingOut(BaseModel):
    project_id: str
    novel_ai_dir: str
    novel_id: str


class ReviewRequest(BaseModel):
    action: str
    task_id: Optional[str] = None
    task_index: Optional[int] = None
    chapter_number: Optional[int] = None
    content: Optional[str] = None
    note: Optional[str] = None


# ─── 规则中心（RuleCenter）───
class RuleConfigOut(BaseModel):
    project_id: str
    style: str
    taboos: list[str] = []
    template: str = "run.章节撰写"
    extra: dict[str, Any] = {}
    updated_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


class RuleConfigUpsert(BaseModel):
    style: Optional[str] = None
    taboos: Optional[list[str]] = None
    template: Optional[str] = None
    extra: Optional[dict[str, Any]] = None


class PostProcessRequest(BaseModel):
    """RuleCenter 后处理工具调用：logic 评估 / venom 毒舌查漏 / deai 去AI痕迹"""
    tool: str                        # logic | venom | deai
    chapter_no: Optional[int] = None # 不传则对最新一章
    style: Optional[str] = None      # 上下文风格（默认 webnovel）
    taboos: Optional[list[str]] = None


class PostProcessResult(BaseModel):
    tool: str
    chapter_no: Optional[int] = None
    summary: str
    findings: list[dict[str, Any]] = []
    score: Optional[float] = None
    cost_usd: float = 0.0
    generated_at: datetime


# ─── 章节扩展 ───
class ChapterCharacterOut(BaseModel):
    id: str
    character_id: str
    character_name: Optional[str] = None
    character_role: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)


# ─── Phase 3：世界构建板块结构化输出 ───
class WorldviewRichOut(BaseModel):
    """GET /projects/{pid}/worldview/rich 的响应"""
    rich: Optional[dict[str, Any]] = None
    story_core: Optional[dict[str, Any]] = None
    history_timeline: Optional[list[dict[str, Any]]] = None
    # 老项目 fallback（world_view_rich_json=null 时用）
    fallback_text: Optional[str] = None
    fallback_story_core: Optional[str] = None


class CharacterSummaryOut(BaseModel):
    """GET /projects/{pid}/characters 列表项"""
    id: str
    name: str
    role: Optional[str] = None
    # 卡片摘要（从 8 段聚合出 2-3 个字段）
    identity: Optional[str] = None   # basic.identity
    age: Optional[str] = None         # basic.age
    gender: Optional[str] = None       # basic.gender


class CharacterCardOut(BaseModel):
    """GET /projects/{pid}/characters/{cid} 详情"""
    id: str
    name: str
    role: Optional[str] = None
    card: Optional[dict[str, Any]] = None
    faction: Optional[dict[str, Any]] = None  # {id, name} 当角色归属势力时


class CharacterRelationOut(BaseModel):
    """GET /projects/{pid}/characters/{cid}/relations 单条边"""
    id: str
    relation: str
    description: Optional[str] = None
    target: dict[str, Any]             # {id, name, role}
    mutual: bool = False
    intensity: Optional[int] = None
    tags: Optional[list[str]] = None
    evolution: Optional[list[dict[str, Any]]] = None
    key_events: Optional[list[dict[str, Any]]] = None


class RelationGraphOut(BaseModel):
    """GET /projects/{pid}/relations/graph"""
    nodes: list[dict[str, Any]]        # [{id, name, role, role_kind}]
    edges: list[dict[str, Any]]        # [{from_id, to_id, relation, mutual, intensity, tags}]


class ChapterFull(BaseModel):
    id: str
    chapter_no: int
    title: Optional[str] = None
    content: str
    # created_at 在历史数据（用 raw SQL 或 _force_reimport 覆盖写入）里可能为空，
    # 避免把整个详情接口打挂，这里允许 None。Schema v2 兼容。
    created_at: Optional[datetime] = None
    characters: list[ChapterCharacterOut] = []

    model_config = ConfigDict(from_attributes=True)


# ─── 伏笔状态 ───
class ForeshadowingStatusUpdate(BaseModel):
    status: str   # 未铺垫 | 已铺垫 | 已回收


# ─── Project AI 参与度 ───
class AiAssistLevelUpdate(BaseModel):
    ai_assist_level: str   # ai_assisted | human_primary | unset


# ─── outline_mode 已合并到 BridgeRunRequest ───
