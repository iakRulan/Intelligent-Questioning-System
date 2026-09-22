from pathlib import Path
from typing import Any
import yaml
from pydantic import BaseModel, Field


class ServiceConfig(BaseModel):
    name: str = "smart-data-service"
    environment: str = "development"
    timezone: str = "Asia/Shanghai"
    host: str = "0.0.0.0"
    port: int = 8080


class LLMConfig(BaseModel):
    base_url: str = "http://127.0.0.1:8000/v1"
    model: str = "qwen-domain-v3"
    api_key: str = "EMPTY"
    timeout_seconds: float = 30.0
    max_concurrency: int = 4


class MySQLConfig(BaseModel):
    dsn: str = "mysql+aiomysql://root:password@127.0.0.1:3306/gt_health"
    pool_size: int = 10
    max_overflow: int = 10


class InfluxDBConfig(BaseModel):
    host: str = "http://127.0.0.1:8181"
    database: str = "turbine"
    token: str = "token_placeholder"
    timeout_seconds: float = 20.0


class QueryPolicy(BaseModel):
    max_result_rows: int = 1000
    hard_max_result_rows: int = 5000
    max_result_bytes: int = 10485760
    query_timeout_seconds: float = 20.0
    explain_timeout_seconds: float = 5.0
    max_metrics_per_query: int = 8
    max_assets_per_query: int = 5
    max_raw_window_hours: int = 168
    max_1m_window_days: int = 31
    max_5m_window_days: int = 180
    max_1h_window_days: int = 1095
    max_auto_repair_attempts: int = 1


class AppConfig(BaseModel):
    service: ServiceConfig = Field(default_factory=ServiceConfig)
    llm: LLMConfig = Field(default_factory=LLMConfig)
    mysql: MySQLConfig = Field(default_factory=MySQLConfig)
    influxdb: InfluxDBConfig = Field(default_factory=InfluxDBConfig)
    query_policy: QueryPolicy = Field(default_factory=QueryPolicy)


def load_config(config_path: str | Path | None = None) -> AppConfig:
    if config_path is None:
        default_yaml = Path(__file__).resolve().parent.parent.parent / "configs" / "application.yaml"
        if default_yaml.exists():
            config_path = default_yaml

    data: dict[str, Any] = {}
    if config_path and Path(config_path).exists():
        with open(config_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}

    return AppConfig(**data)


# 全局配置单例
settings = load_config()
