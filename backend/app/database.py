from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker, declarative_base

from .config import settings

engine = create_engine(
    settings.database_url,
    connect_args={"check_same_thread": False} if settings.database_url.startswith("sqlite") else {},
)


# ─────────────────────────────────────────────
# SQLite 性能调优（迭代 #10）
# ─────────────────────────────────────────────
# WAL (Write-Ahead Logging) 让读写并发不互锁：
#   - 默认 rollback journal：写操作期间全库锁，读阻塞
#   - WAL：读 + 写同时进行（写只追加 .wal 文件，读看 snapshot）
#   - engine 写 state.json 时前端 /health 不会卡
#
# busy_timeout：SQLite 等锁超时（默认 0 = 不等，立刻抛）
#   - 设 5s 后 5s 内自动重试（避免 lock acquired 假错误）
# synchronous=NORMAL：WAL 模式下 fsync 频率降低（写吞吐 ↑30-50%）
#   - 断电可能丢最后几个 commit（prototype 可接受）
if settings.database_url.startswith("sqlite"):
    @event.listens_for(engine, "connect")
    def _set_sqlite_pragma(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        # WAL 模式（持久化，需要对每个新连接设）
        cursor.execute("PRAGMA journal_mode=WAL")
        # 等锁 5 秒
        cursor.execute("PRAGMA busy_timeout=5000")
        # WAL 模式下 NORMAL 是推荐折衷
        cursor.execute("PRAGMA synchronous=NORMAL")
        # 外键约束（默认是 OFF，必须显式开）
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
