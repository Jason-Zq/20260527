"""
数据库引擎模块
提供异步引擎、session 工厂和同步引擎（供 Alembic 使用）。
"""

import os
import json
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy import create_engine, text

# 配置文件路径
CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "config.json")


def _load_db_config() -> dict:
    """从 config.json 加载数据库配置。"""
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        config = json.load(f)
    return config.get("database", {})


def _build_dsn(driver: str = "postgresql+asyncpg") -> str:
    """根据配置构建数据库连接字符串。"""
    db = _load_db_config()
    host = db.get("host", "localhost")
    port = db.get("port", 5432)
    user = db.get("user", "postgres")
    password = db.get("password", "postgres")
    dbname = db.get("dbname", "doc_review")
    return f"{driver}://{user}:{password}@{host}:{port}/{dbname}"


# 异步引擎（FastAPI 运行时使用）
async_engine = create_async_engine(
    _build_dsn("postgresql+asyncpg"),
    echo=False,
    pool_size=10,
    max_overflow=20,
    pool_pre_ping=True,
    pool_recycle=1800,
)

# 异步 session 工厂
async_session_maker = async_sessionmaker(
    async_engine,
    class_=AsyncSession,
    expire_on_commit=False,
)

# 同步引擎（Alembic 迁移使用）
sync_engine = create_engine(
    _build_dsn("postgresql+psycopg2"),
    echo=False,
)


async def init_db():
    """初始化数据库连接池，验证连接可用性。"""
    async with async_engine.connect() as conn:
        result = await conn.execute(text("SELECT 1"))
        result.fetchone()
    print("数据库连接池已初始化")


async def get_session() -> AsyncSession:
    """获取一个数据库 session（用于依赖注入）。"""
    async with async_session_maker() as session:
        yield session
