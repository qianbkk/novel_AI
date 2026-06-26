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

    class Config:
        from_attributes = True


class JobOut(BaseModel):
    id: str
    project_id: str
    status: str
    current_stage: Optional[str]
    progress_percent: int

    class Config:
        from_attributes = True


class ChapterCreate(BaseModel):
    chapter_no: int
    title: Optional[str] = None
    content: str


class ProviderCreate(BaseModel):
    name: str
    provider_type: str
    api_base: Optional[str] = None
    api_key: str
    default_model: str
    extra_json: Optional[dict[str, Any]] = None
    needs_proxy: bool = False


class ProviderOut(BaseModel):
    id: str
    name: str
    provider_type: str
    api_base: Optional[str] = None
    default_model: str
    extra_json: Optional[dict[str, Any]] = None
    needs_proxy: bool = False
    created_at: datetime

    class Config:
        from_attributes = True


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

    class Config:
        from_attributes = True


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
