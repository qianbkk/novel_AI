"""
全局配置。

数据存储默认落在 backend/data/ 目录下的 SQLite 文件——用绝对路径锚定到
这个文件所在的项目目录，而不是"运行 uvicorn 时所在的当前目录"，避免
"在 A 目录跑起来一份数据库、换个目录跑又起了一份新数据库"这种最常见的
本地踩坑。所有项目数据（世界观/人物/章节/向量等）都在这一个文件里，
没有依赖任何外部数据库服务或云存储。

注意：MiniMax / DeepSeek 等国内服务通常不需要代理；
如果以后切换到海外模型（如直连 Anthropic / OpenAI），
把 LLM_PROXY_URL 填上 http://127.0.0.1:7890 之类即可，
llm_client.py 会自动给 httpx 客户端加上代理。

**配置单一真相源原则**：
所有"识别项目会用到的环境变量"都集中在这里。其他模块应：
  - 通过 `from app.config import settings` 读（推荐）
  - 或直接读 env（向后兼容老代码）
两种方式并存，但任何**新**配置项必须加到这里并标注默认值 / 用途，
不要再散落 `os.environ.get(...)`。

env 变量命名：
  - 默认走 NOVEL_ 前缀（可改 model_config.env_prefix 调整）
  - 但留 `validation_alias` 让裸名（ALLOWED_ORIGINS、RATE_LIMIT_PER_MINUTE 等）
    也能覆盖（向后兼容 main.py/security.py/middleware/rate_limit.py 里的
    散落读取，因为不强制每一处都迁过来）。
"""
from pathlib import Path

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent  # backend/ 目录
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)


class Settings(BaseSettings):
    # ── 数据库 ──
    # 数据库：固定存在 backend/data/novel_assistant.db
    database_url: str = Field(
        default=f"sqlite:///{DATA_DIR / 'novel_assistant.db'}",
        validation_alias=AliasChoices("DATABASE_URL", "NOVEL_DATABASE_URL"),
        description="SQLAlchemy database URL. Tests and deployments may override the local SQLite default.",
    )

    # ── LLM 默认（mock 模式下全局生效，忽略下面的角色路由）──
    llm_provider: str = "mock"          # mock | deepseek | kimi | minimax | openai_compatible
    llm_api_base: str = ""
    llm_api_key: str = ""
    llm_model: str = ""

    # ── 按角色路由的 provider（2026.6 横评结论）──
    # DeepSeek 逻辑强但"文风理工味重"，只适合结构化/大纲类阶段；
    # Kimi 文风偏文学性，适合需要"味道"的细节生成；
    # MiniMax 走 Lightning Attention 长窗口、国内服务不需代理，适合一致性复核。
    # 某个角色没配置 key 时自动退回 llm_provider，不强制要求三个都配置。
    deepseek_api_base: str = "https://api.deepseek.com/v1"
    deepseek_api_key: str = ""
    deepseek_model: str = "deepseek-chat"

    kimi_api_base: str = "https://api.moonshot.cn/v1"
    kimi_api_key: str = ""
    kimi_model: str = "kimi-k2"

    # 迭代 #52: 之前默认 "https://api.minimax.chat/v1" 是旧版 endpoint，
    # 现在 MiniMax M3 用 api.minimaxi.com（per iter #32 router.py 注释）。
    minimax_api_base: str = "https://api.minimaxi.com/v1"
    minimax_api_key: str = ""
    minimax_model: str = "MiniMax-M3"

    # ── Embedding provider ──
    # 用于向量检索层。默认 Qwen3-Embedding（阿里云百炼 OpenAI 兼容接口），
    # 用户只需设 NOVEL_EMBEDDING_API_KEY 就走真模型；不设 key 自动 fallback
    # 到 mock（256 维字符 bigram hash），offline dev 友好。
    # 可选值：qwen3 | bge_m3 | openai_compatible | mock
    # 真实实战选项参考 backend/app/rag/embedding.py 顶部注释。
    embedding_provider: str = "qwen3"
    embedding_api_base: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    embedding_api_key: str = ""
    embedding_model: str = "text-embedding-v3"

    # ── 代理 ──
    # 仅对需要走代理的海外服务生效。DeepSeek/Kimi/MiniMax 均为国内服务，不需要代理。
    llm_proxy_url: str = ""             # 例如 http://127.0.0.1:7890

    # ── 生成参数 ──
    llm_timeout_seconds: int = 120
    llm_max_retries: int = 2

    # ── HTTP / 安全 ──
    # 全部走 NOVEL_ 前缀，但留 validation_alias 让 ALLOWED_ORIGINS 等历史
    # 裸 env 也兼容（向后兼容 main.py 等直接读 env 的代码）。
    allowed_origins: str = Field(
        default="http://localhost:5293,http://127.0.0.1:5293",
        validation_alias=AliasChoices("ALLOWED_ORIGINS", "NOVEL_ALLOWED_ORIGINS"),
        description="CORS 白名单（逗号分隔）。生产部署务必收紧到实际前端域名。",
    )
    rate_limit_per_minute: int = Field(
        default=60,
        validation_alias=AliasChoices("RATE_LIMIT_PER_MINUTE", "NOVEL_RATE_LIMIT_PER_MINUTE"),
        description="写端点每 IP 每分钟最大请求数。生产建议调到 10-20。",
    )
    rate_limit_exempt_localhost: bool = Field(
        default=True,
        validation_alias=AliasChoices("RATE_LIMIT_EXEMPT_LOCALHOST", "NOVEL_RATE_LIMIT_EXEMPT_LOCALHOST"),
        description="本地 (127.0.0.1 / ::1) 写端点不限流（个人原型阶段）。真暴露公网时设 0 关掉。",
    )
    allowed_proxies: str = Field(
        default="",
        validation_alias=AliasChoices("ALLOWED_PROXIES", "NOVEL_ALLOWED_PROXIES"),
        description="反代 IP 白名单（逗号分隔 CIDR/IP）。反代场景下必填，否则 XFF 不可信。",
    )
    novel_production: bool = Field(
        default=False,
        validation_alias=AliasChoices("NOVEL_PRODUCTION"),
        description="生产模式：启动时强制要求 MASTER_KEY 已设置（fail-fast）。",
    )

    # ── 备份 ──
    skip_backup: bool = Field(
        default=False,
        validation_alias=AliasChoices("NOVEL_AI_SKIP_BACKUP"),
        description="启动时跳过 SQLite 快照（调试用）。",
    )
    backup_keep_n: int = Field(
        default=10,
        validation_alias=AliasChoices("NOVEL_AI_BACKUP_KEEP_N"),
        description="SQLite 快照保留份数。",
    )

    # ── 加密 ──
    master_key: str = Field(
        default="",
        validation_alias=AliasChoices("MASTER_KEY", "NOVEL_MASTER_KEY"),
        description="Provider API key 加密的 Fernet key。未设 → dev 模式自动生成持久化。",
    )

    # ── 引擎 ──
    novel_engine_mock: bool = Field(
        default=False,
        validation_alias=AliasChoices("NOVEL_ENGINE_MOCK"),
        description="强制所有 9 个写作 agent 走 mock provider（无需 API key 即可端到端跑通）。",
    )
    novel_outline_mode: str = Field(
        default="batch",
        validation_alias=AliasChoices("NOVEL_OUTLINE_MODE"),
        description="Outline 模式：batch (传统批量) | card (3 候选抽卡) | talk (交互头脑风暴)",
    )
    engine_timeout_min: int = Field(
        default=120,
        validation_alias=AliasChoices("NOVEL_ENGINE_TIMEOUT_MIN"),
        description="security-2026-07-13 #3: 引擎子进程最大无 stdout 空闲时间（分钟）。"
                    "超过此时间看门狗会 SIGTERM 整个进程组。",
    )

    # Pydantic V3 会删 class-based config，迁到 SettingsConfigDict
    # 见 https://errors.pydantic.dev/2.9/migration/
    model_config = SettingsConfigDict(env_file=".env", env_prefix="NOVEL_", extra="ignore")


settings = Settings()


def get_allowed_origins_list() -> list[str]:
    """把 ALLOWED_ORIGINS env / settings 解析成 list[str]。
    直接给 starlette CORSMiddleware 用（它要 list）。
    """
    raw = settings.allowed_origins or ""
    if not raw.strip():
        return ["http://localhost:5293", "http://127.0.0.1:5293"]
    return [o.strip() for o in raw.split(",") if o.strip()]


def list_env_keys() -> list[dict]:
    """运维用：列出本 Settings 类识别的全部 env key + 默认值。
    给 docs/README 用作「项目支持的环境变量」清单。
    """
    out = []
    for name, field in settings.model_fields.items():
        aliases = field.validation_alias
        # AliasChoices 是 pydantic 的高阶类型，choices 可迭代
        if isinstance(aliases, str):
            alias_list = [aliases]
        elif aliases is None:
            alias_list = []
        elif hasattr(aliases, "choices"):
            alias_list = list(aliases.choices)
        else:
            try:
                alias_list = list(aliases)
            except TypeError:
                alias_list = [str(aliases)]
        out.append({
            "field": name,
            "aliases": alias_list,
            "default": field.default if field.default is not None else None,
            "description": (field.description or "")[:80],
        })
    return out
