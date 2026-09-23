from __future__ import annotations

import os
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
    cors_origins: list[str] = Field(default_factory=lambda: ["*"])


class LLMConfig(BaseModel):
    enabled: bool = True
    base_url: str = "https://api.deepseek.com/v1"
    model: str = "deepseek-flash"
    api_key: str = "EMPTY"
    timeout_seconds: float = 45.0
    max_concurrency: int = 4
    reasoning_effort: str = "low"


class MySQLConfig(BaseModel):
    dsn: str = "mysql+aiomysql://root:password@127.0.0.1:3306/gt_health"
    pool_size: int = 10
    max_overflow: int = 10


class InfluxDBConfig(BaseModel):
    host: str = "http://tddb.a1.luyouxia.net:26305"
    database: str = "szt_flux"
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


class PipelineConfig(BaseModel):
    version: str = "0.1.0"
    telemetry_enabled: bool = False
    event_queue_size: int = 100
    mode: str = "auto"


class AppConfig(BaseModel):
    service: ServiceConfig = Field(default_factory=ServiceConfig)
    llm: LLMConfig = Field(default_factory=LLMConfig)
    mysql: MySQLConfig = Field(default_factory=MySQLConfig)
    influxdb: InfluxDBConfig = Field(default_factory=InfluxDBConfig)
    query_policy: QueryPolicy = Field(default_factory=QueryPolicy)
    pipeline: PipelineConfig = Field(default_factory=PipelineConfig)


def _deep_update(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _deep_update(base[key], value)
        else:
            base[key] = value
    return base


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with open(path, "r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    if not isinstance(data, dict):
        raise ValueError(f"配置文件必须是 YAML 映射: {path}")
    return data


def _apply_env_overrides(data: dict[str, Any]) -> dict[str, Any]:
    env_map = {
        "SMART_DATA_ENV": ("service", "environment"),
        "SMART_DATA_MODE": ("pipeline", "mode"),
        "LLM_BASE_URL": ("llm", "base_url"),
        "LLM_MODEL": ("llm", "model"),
        "LLM_API_KEY": ("llm", "api_key"),
        "LLM_REASONING_EFFORT": ("llm", "reasoning_effort"),
        "MYSQL_DSN": ("mysql", "dsn"),
        "INFLUXDB_HOST": ("influxdb", "host"),
        "INFLUXDB_DATABASE": ("influxdb", "database"),
        "INFLUXDB_TOKEN": ("influxdb", "token"),
    }
    for env_name, (section, field) in env_map.items():
        value = os.getenv(env_name)
        if value:
            data.setdefault(section, {})[field] = value
    enabled = os.getenv("SMART_DATA_LLM_ENABLED")
    if enabled is not None:
        data.setdefault("llm", {})["enabled"] = enabled.strip().lower() in {"1", "true", "yes", "on"}
    return data


def _is_placeholder_secret(value: str) -> bool:
    lowered = (value or "").strip().lower()
    return lowered in {"", "empty", "token_placeholder", "changeme", "password"}


def validate_startup(config: AppConfig) -> None:
    os.environ["HAYSTACK_TELEMETRY_ENABLED"] = "False"
    if config.pipeline.telemetry_enabled:
        raise SystemExit("Haystack 遥测必须关闭：pipeline.telemetry_enabled=false")

    live_mode = config.pipeline.mode not in {"mock", "development"}
    production = config.service.environment == "production"
    if production and live_mode:
        if _is_placeholder_secret(config.influxdb.token):
            raise SystemExit("生产 live 模式缺少 INFLUXDB_TOKEN，拒绝带病启动")
        if _is_placeholder_secret(config.llm.api_key) and "127.0.0.1" not in config.llm.base_url:
            raise SystemExit("生产 live 模式缺少 LLM_API_KEY，拒绝带病启动")


def _load_dotenv(root: Path) -> None:
    env_file = root / ".env"
    if not env_file.exists():
        return
    for raw in env_file.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def load_config(config_path: str | Path | None = None) -> AppConfig:
    root = Path(__file__).resolve().parent.parent.parent
    _load_dotenv(root)
    app_yaml = Path(config_path) if config_path else root / "configs" / "application.yaml"
    policy_yaml = root / "configs" / "policies.yaml"

    data: dict[str, Any] = {}
    if app_yaml.exists():
        data = _load_yaml(app_yaml)
    if policy_yaml.exists():
        _deep_update(data, _load_yaml(policy_yaml))
    _apply_env_overrides(data)

    config = AppConfig(**data)
    validate_startup(config)
    return config


settings = load_config()
