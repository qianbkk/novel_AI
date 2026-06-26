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
"""
from pathlib import Path

from pydantic_settings import BaseSettings

BASE_DIR = Path(__file__).resolve().parent.parent  # backend/ 目录
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)


class Settings(BaseSettings):
    # 数据库：固定存在 backend/data/novel_assistant.db
    database_url: str = f"sqlite:///{DATA_DIR / 'novel_assistant.db'}"

    # 默认 LLM 提供方（mock 模式下全局生效，忽略下面的角色路由）
    llm_provider: str = "mock"          # mock | deepseek | kimi | minimax | openai_compatible
    llm_api_base: str = ""
    llm_api_key: str = ""
    llm_model: str = ""

    # 按角色路由的 provider，对应 2026.6 的横评结论：
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

    minimax_api_base: str = "https://api.minimax.chat/v1"
    minimax_api_key: str = ""
    minimax_model: str = "minimax-text-01"

    # Embedding provider，用于向量检索层。国内可选 Qwen3-Embedding（阿里云百炼）
    # 或本地部署 BGE-M3；都不需要代理。mock 模式下用字符 bigram 哈希代替真实
    # embedding，足够跑通"重复度检测"这条链路，不需要联网。
    embedding_provider: str = "mock"          # mock | qwen3 | bge_m3 | openai_compatible
    embedding_api_base: str = ""
    embedding_api_key: str = ""
    embedding_model: str = "text-embedding-v3"

    # 代理：仅对需要走代理的海外服务生效。DeepSeek/Kimi/MiniMax 均为国内服务，不需要代理。
    llm_proxy_url: str = ""             # 例如 http://127.0.0.1:7890

    # 生成参数
    llm_timeout_seconds: int = 120
    llm_max_retries: int = 2

    class Config:
        env_file = ".env"
        env_prefix = "NOVEL_"


settings = Settings()
