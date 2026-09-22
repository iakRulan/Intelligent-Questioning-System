from typing import Any
from pydantic import BaseModel, Field
from src.smart_data.domain.intent import QueryIntent, TimeRange


class MetricRef(BaseModel):
    business_name: str = Field(description="标准业务名称，如 平均排气温度")
    point_code: str = Field(description="测点编码，如 T4_AVG")
    measurement: str = Field(description="InfluxDB 表名，如 gt_exhaust")
    field: str = Field(description="时序数据字段，如 temperature")
    unit: str | None = Field(default=None, description="物理单位，如 ℃, rpm, MW")
    aggregation: str | None = Field(default=None, description="默认或指定聚合函数")


class QueryState(BaseModel):
    query_id: str
    trace_id: str
    conversation_id: str | None = None
    user_id: str
    question: str
    allowed_asset_ids: list[str] = Field(default_factory=list, description="用户拥有的机组权限列表")

    # 阶段 1 输出
    intent: QueryIntent | None = None

    # 阶段 2 输出
    metrics: list[MetricRef] = Field(default_factory=list)
    dictionary_version: str | None = None

    # 阶段 3 输出
    generated_sql: str | None = None
    prompt_versions: dict[str, str] = Field(default_factory=dict)
    model_version: str | None = None

    # 阶段 4 输出
    safe_sql: str | None = None
    security_passed: bool = False

    # 阶段 5 输出
    raw_columns: list[str] = Field(default_factory=list)
    raw_records: list[dict[str, Any]] = Field(default_factory=list)
    row_count: int = 0
    query_elapsed_ms: float = 0.0

    # 阶段 6 输出
    statistics: dict[str, Any] = Field(default_factory=dict)
    visualization: dict[str, Any] = Field(default_factory=dict)
    conclusion: str | None = None
    warnings: list[str] = Field(default_factory=list)

    # 中途错误或取消
    error_code: str | None = None
    error_message: str | None = None
    cancelled: bool = False
