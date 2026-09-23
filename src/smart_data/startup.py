from __future__ import annotations

import logging

from src.smart_data.config import settings
from src.smart_data.runtime import set_query_backend

logger = logging.getLogger(__name__)


def startup_datasources() -> str:
    """按配置探测并接入 MySQL / Influx，失败时回退 mock。"""
    mode = (settings.pipeline.mode or "auto").lower()
    if mode == "mock":
        set_query_backend("mock")
        logger.info("查询后端: mock")
        return "mock"

    if mode in {"mysql", "auto"}:
        from src.smart_data.infrastructure.mysql.bootstrap import bootstrap_mysql

        if bootstrap_mysql():
            set_query_backend("mysql")
            logger.info("查询后端: mysql")
            return "mysql"
        if mode == "mysql":
            set_query_backend("mock")
            logger.warning("MySQL 不可用，已回退 mock")
            return "mock"

    if mode in {"influx", "auto"}:
        from src.smart_data.infrastructure.influx.client import InfluxHttpClient

        if InfluxHttpClient().ping():
            set_query_backend("influx")
            logger.info("查询后端: influx")
            return "influx"

    set_query_backend("mock")
    logger.warning("未接入外部数据源，查询后端: mock")
    return "mock"
