from __future__ import annotations

from functools import lru_cache

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.engine.url import make_url

from src.smart_data.config import settings


def _sync_dsn(dsn: str) -> str:
    if dsn.startswith("mysql+aiomysql://"):
        return "mysql+pymysql://" + dsn[len("mysql+aiomysql://") :]
    if dsn.startswith("mysql://"):
        return "mysql+pymysql://" + dsn[len("mysql://") :]
    return dsn


def ensure_database() -> None:
    import re

    url = make_url(_sync_dsn(settings.mysql.dsn))
    if not url.database:
        return
    if not re.fullmatch(r"[A-Za-z0-9_]+", url.database):
        raise ValueError(f"非法数据库名: {url.database}")
    admin_engine = create_engine(url.set(database="mysql"), future=True)
    try:
        with admin_engine.begin() as conn:
            conn.execute(
                text(
                    f"CREATE DATABASE IF NOT EXISTS `{url.database}` "
                    "DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
                )
            )
    finally:
        admin_engine.dispose()
    get_engine.cache_clear()


@lru_cache(maxsize=1)
def get_engine() -> Engine:
    return create_engine(
        _sync_dsn(settings.mysql.dsn),
        pool_size=settings.mysql.pool_size,
        max_overflow=settings.mysql.max_overflow,
        pool_pre_ping=True,
        pool_recycle=3600,
        future=True,
    )


def ping_mysql() -> bool:
    try:
        with get_engine().connect() as conn:
            conn.exec_driver_sql("SELECT 1")
        return True
    except Exception:
        return False
