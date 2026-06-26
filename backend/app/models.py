"""
表设计对应参考截图里左侧的"世界素材"分类：
世界观设定 / 故事核心 / 人物 / 势力 / 力量体系 / 地图 / 货币体系 / 伏笔管理

为了让后续"大纲生成"和"章节生成"能引用这些实体（而不是用名字硬编码),
每个实体都有自己的 UUID 主键。
"""
import uuid
from datetime import datetime

from sqlalchemy import (
    Column, String, Text, Integer, ForeignKey, DateTime, JSON, Float, Boolean
)
from sqlalchemy.orm import relationship

from .database import Base


def gen_id() -> str:
    return uuid.uuid4().hex


class Project(Base):
    __tablename__ = "projects"

    id = Column(String, primary_key=True, default=gen_id)
    title = Column(String, nullable=True)            # 留空则 AI 自动取名
    genre = Column(String, nullable=False)            # 玄幻/都市/科幻...
    audience = Column(String, nullable=True)          # 男频·青年向 等
    config_json = Column(JSON, nullable=False)         # 构建配置原始表单(套路/篇幅/结构模式等)
    status = Column(String, default="draft")           # draft | worldbuilding | ready
    # AI 参与度声明：对应《人工智能生成合成内容标识办法》(2025-09-01施行)
    # 以及番茄/晋江等平台对"批量AI生成"的从严整治——这里先把字段留出来，
    # 后续做章节生成时，每章应该有自己的 ai_assist_level + human_edit_ratio，
    # 项目级别这个字段是默认值/汇总展示用。
    ai_assist_level = Column(String, default="ai_assisted")  # ai_assisted | human_primary | unset
    budget_limit_usd = Column(Float, nullable=True)
    novel_ai_status = Column(String, default="not_started")
    # not_started | concept_pushed | planner_done | bootstrap_done | writing | done
    created_at = Column(DateTime, default=datetime.utcnow)

    world_setting = relationship("WorldSetting", back_populates="project", uselist=False)
    characters = relationship("Character", back_populates="project")
    factions = relationship("Faction", back_populates="project")
    power_systems = relationship("PowerSystem", back_populates="project")
    map_nodes = relationship("MapNode", back_populates="project")
    foreshadowings = relationship("Foreshadowing", back_populates="project")
    currencies = relationship("Currency", back_populates="project")
    entity_relations = relationship("EntityRelation", back_populates="project")
    jobs = relationship("GenerationJob", back_populates="project")


class WorldSetting(Base):
    """世界观设定 + 故事核心 + 情节脉络骨架 + 特殊设定，先放一张表，简单够用"""
    __tablename__ = "world_settings"

    id = Column(String, primary_key=True, default=gen_id)
    project_id = Column(String, ForeignKey("projects.id"), unique=True)
    world_view = Column(Text, nullable=True)            # 世界观设定
    story_core = Column(Text, nullable=True)            # 故事核心/主要冲突
    plot_skeleton_json = Column(JSON, nullable=True)    # 卷级情节脉络（粗粒度，章节级留给大纲阶段）
    special_settings_json = Column(JSON, nullable=True) # 特殊设定（金手指类型等）
    novel_ai_raw_setting_json = Column(JSON, nullable=True)  # novel_AI 回灌的原始设定包，唯一真相来源

    project = relationship("Project", back_populates="world_setting")


class Character(Base):
    __tablename__ = "characters"

    id = Column(String, primary_key=True, default=gen_id)
    project_id = Column(String, ForeignKey("projects.id"))
    name = Column(String, nullable=False)
    role = Column(String, nullable=True)        # 主角/重要配角/反派...
    detail_json = Column(JSON, nullable=True)   # 身世/能力/动机等结构化细节

    project = relationship("Project", back_populates="characters")


class Faction(Base):
    __tablename__ = "factions"

    id = Column(String, primary_key=True, default=gen_id)
    project_id = Column(String, ForeignKey("projects.id"))
    name = Column(String, nullable=False)
    detail_json = Column(JSON, nullable=True)

    project = relationship("Project", back_populates="factions")


class PowerSystem(Base):
    __tablename__ = "power_systems"

    id = Column(String, primary_key=True, default=gen_id)
    project_id = Column(String, ForeignKey("projects.id"))
    name = Column(String, nullable=False)
    description = Column(Text, nullable=True)
    tiers_json = Column(JSON, nullable=True)   # 境界体系，对应截图里的1~6阶

    project = relationship("Project", back_populates="power_systems")


class MapNode(Base):
    """地理地图：自引用树，世界->大陆->省->市->区->街->地点"""
    __tablename__ = "map_nodes"

    id = Column(String, primary_key=True, default=gen_id)
    project_id = Column(String, ForeignKey("projects.id"))
    parent_id = Column(String, ForeignKey("map_nodes.id"), nullable=True)
    name = Column(String, nullable=False)
    level = Column(String, nullable=False)     # world/continent/province/city/district/street/place
    description = Column(Text, nullable=True)

    project = relationship("Project", back_populates="map_nodes")
    children = relationship("MapNode")


class Foreshadowing(Base):
    __tablename__ = "foreshadowings"

    id = Column(String, primary_key=True, default=gen_id)
    project_id = Column(String, ForeignKey("projects.id"))
    content = Column(Text, nullable=False)
    linked_character_id = Column(String, ForeignKey("characters.id"), nullable=True)
    importance = Column(String, default="中")   # 高/中/低，对应截图里的"伏笔等级"
    status = Column(String, default="未铺垫")     # 未铺垫/已铺垫/已回收
    planted_chapter_hint = Column(String, nullable=True)   # 预计铺垫章节区间，后续大纲阶段细化
    payoff_chapter_hint = Column(String, nullable=True)

    project = relationship("Project", back_populates="foreshadowings")


class Currency(Base):
    __tablename__ = "currencies"

    id = Column(String, primary_key=True, default=gen_id)
    project_id = Column(String, ForeignKey("projects.id"))
    name = Column(String, nullable=False)
    detail_json = Column(JSON, nullable=True)

    project = relationship("Project", back_populates="currencies")


class EntityRelation(Base):
    """
    实体关系图谱：不只存"角色"本身，还存角色与角色/势力之间的关系边。
    对标参考产品里的"人物关系图"，也是马良写作"自动提取实体并构建动态
    关系图谱"那一功能的本地实现——写作时按需检索关系边，而不是把所有
    人物设定全部塞进 prompt（详见 README 里"为什么不直接靠长上下文"）。
    """
    __tablename__ = "entity_relations"

    id = Column(String, primary_key=True, default=gen_id)
    project_id = Column(String, ForeignKey("projects.id"))
    from_type = Column(String, nullable=False)   # character | faction
    from_id = Column(String, nullable=False)
    to_type = Column(String, nullable=False)
    to_id = Column(String, nullable=False)
    relation = Column(String, nullable=False)     # 青梅竹马/师徒/宿敌/上下级...
    description = Column(Text, nullable=True)

    project = relationship("Project", back_populates="entity_relations")


class Chapter(Base):
    """
    章节正文。现在只是个轻量级落地点（还没有真正的章节生成流水线），
    主要是为了让向量检索层有真实内容可以 embed——RAG 这层的价值要等
    有正文之后才能体现，光有世界观设定无法演示"重复度检测"这类场景。
    """
    __tablename__ = "chapters"

    id = Column(String, primary_key=True, default=gen_id)
    project_id = Column(String, ForeignKey("projects.id"))
    chapter_no = Column(Integer, nullable=False)
    title = Column(String, nullable=True)
    content = Column(Text, nullable=False)
    summary = Column(Text, nullable=True)
    ai_assist_level = Column(String, default="ai_assisted")  # 章节级合规字段，呼应 Project 同名字段
    created_at = Column(DateTime, default=datetime.utcnow)

    project = relationship("Project")


class ChapterCharacter(Base):
    """章节 <-> 人物 的图谱边：这一章出现了哪些人物。语义检索按角色过滤候选章节时会用到。"""
    __tablename__ = "chapter_characters"

    id = Column(String, primary_key=True, default=gen_id)
    chapter_id = Column(String, ForeignKey("chapters.id"))
    character_id = Column(String, ForeignKey("characters.id"))


class EmbeddingChunk(Base):
    """
    向量检索层的存储表。先用普通 SQLite 表 + Python 里算 cosine similarity，
    个人项目这点数据量（几百章）暴力计算毫秒级就能跑完，不需要上 sqlite-vec
    这类向量扩展——等数据规模真的上来了再换存储后端，上层的查询函数签名
    不需要变。
    """
    __tablename__ = "embedding_chunks"

    id = Column(String, primary_key=True, default=gen_id)
    project_id = Column(String, ForeignKey("projects.id"))
    source_type = Column(String, nullable=False)   # chapter | character | foreshadowing
    source_id = Column(String, nullable=False)
    text_snippet = Column(Text, nullable=False)     # 存一份原文片段方便调试/展示，不止存向量
    embedding_json = Column(JSON, nullable=False)
    model = Column(String, default="mock-ngram")
    created_at = Column(DateTime, default=datetime.utcnow)


class Provider(Base):
    """用户配置的供应商账号，一个账号可被多个角色复用"""
    __tablename__ = "providers"

    id = Column(String, primary_key=True, default=gen_id)
    name = Column(String, nullable=False)
    provider_type = Column(String, nullable=False)  # anthropic|deepseek|gemini|kimi|minimax|custom
    api_base = Column(String, nullable=True)
    api_key = Column(String, nullable=False)
    default_model = Column(String, nullable=False)
    extra_json = Column(JSON, nullable=True)
    needs_proxy = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)


class RoleAssignment(Base):
    """角色 -> Provider 的绑定，覆盖 novel-assistant 自身3个角色 + novel_AI 的12个 Agent"""
    __tablename__ = "role_assignments"

    id = Column(String, primary_key=True, default=gen_id)
    role_key = Column(String, nullable=False, unique=True)
    provider_id = Column(String, ForeignKey("providers.id"), nullable=True)
    model_override = Column(String, nullable=True)


class BridgeRun(Base):
    """每次调起 novel_AI 的执行记录"""
    __tablename__ = "bridge_runs"

    id = Column(String, primary_key=True, default=gen_id)
    project_id = Column(String, ForeignKey("projects.id"))
    command = Column(String, nullable=False)
    args_json = Column(JSON, nullable=True)
    status = Column(String, default="pending")
    exit_code = Column(Integer, nullable=True)
    stdout_text = Column(Text, nullable=True)
    started_at = Column(DateTime, default=datetime.utcnow)
    finished_at = Column(DateTime, nullable=True)


class NovelAIBinding(Base):
    """Project 和 novel_AI 工作目录/novel_id 的绑定关系"""
    __tablename__ = "novel_ai_bindings"

    id = Column(String, primary_key=True, default=gen_id)
    project_id = Column(String, ForeignKey("projects.id"), unique=True)
    novel_ai_dir = Column(String, nullable=False)
    novel_id = Column(String, nullable=False)


class GenerationJob(Base):
    """记录一次"世界构建"任务的进度，前端用 SSE 订阅这张表对应的事件流"""
    __tablename__ = "generation_jobs"

    id = Column(String, primary_key=True, default=gen_id)
    project_id = Column(String, ForeignKey("projects.id"))
    job_type = Column(String, default="worldbuild")
    status = Column(String, default="pending")   # pending/running/done/failed
    current_stage = Column(String, nullable=True)
    progress_percent = Column(Integer, default=0)
    error_message = Column(Text, nullable=True)
    consistency_warnings_json = Column(JSON, nullable=True)  # 一致性校验阶段产出的"吃书风险"清单
    created_at = Column(DateTime, default=datetime.utcnow)

    project = relationship("Project", back_populates="jobs")
