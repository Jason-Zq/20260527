"""
Alembic 迁移环境配置
从 config.json 动态读取数据库连接信息。
"""

import os
import sys
import json
from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool
from alembic import context

# 将 backend/ 加入 sys.path，以便导入 models
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from db.models import Base

# Alembic Config 对象
config = context.config

# 日志配置
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# 数据库连接:优先 DATABASE_URL 环境变量,否则 config.json
env_url = os.getenv("DATABASE_URL", "").strip()
if env_url:
    # alembic 用 psycopg2 同步驱动,把 asyncpg 前缀换掉
    if env_url.startswith("postgresql+asyncpg://"):
        db_url = "postgresql+psycopg2://" + env_url[len("postgresql+asyncpg://"):]
    elif env_url.startswith("postgresql://"):
        db_url = "postgresql+psycopg2://" + env_url[len("postgresql://"):]
    else:
        db_url = env_url
else:
    config_path = os.path.join(os.path.dirname(__file__), "..", "config.json")
    with open(config_path, "r", encoding="utf-8") as f:
        app_config = json.load(f)
    db_conf = app_config.get("database", {})
    db_url = "postgresql+psycopg2://{user}:{password}@{host}:{port}/{dbname}".format(
        user=db_conf.get("user", "postgres"),
        password=db_conf.get("password", "postgres"),
        host=db_conf.get("host", "localhost"),
        port=db_conf.get("port", 5432),
        dbname=db_conf.get("dbname", "doc_review"),
    )
config.set_main_option("sqlalchemy.url", db_url)

# 目标 metadata（用于 autogenerate）
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """离线模式运行迁移（生成 SQL 脚本）。"""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """在线模式运行迁移（连接数据库执行）。"""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
