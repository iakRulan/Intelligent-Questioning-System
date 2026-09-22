from typing import Literal
from datetime import datetime
from pydantic import BaseModel, Field


class TimeRange(BaseModel):
    start: datetime
    end: datetime
    timezone: str = "Asia/Shanghai"


class QueryIntent(BaseModel):
    operation: Literal["query", "aggregate", "compare", "extreme", "correlation"] = "query"
    asset_ids: list[str] = Field(default_factory=list, description="机组资产编码，如 GT-001, #1")
    metric_terms: list[str] = Field(default_factory=list, description="自然语言提及的指标词汇，如 排温, 转速")
    time_range: TimeRange | None = Field(default=None, description="时间范围")
    aggregation: str | None = Field(default=None, description="聚合函数，如 avg, max, min, sum")
    group_by: list[str] = Field(default_factory=list, description="分组维度，如 day, hour, asset")
    needs_clarification: bool = Field(default=False, description="是否缺少关键信息需要澄清")
    clarification_questions: list[str] = Field(default_factory=list, description="需要向用户澄清的问题列表")
