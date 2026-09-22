from typing import Any, Literal
from pydantic import BaseModel, Field


class ChartSeries(BaseModel):
    field: str
    name: str
    unit: str | None = None
    chart_type: str = "line"


class ChartAxis(BaseModel):
    field: str
    type: Literal["time", "category", "value"] = "time"


class ChartDSL(BaseModel):
    type: Literal["line", "bar", "scatter", "kpi", "area"] = "line"
    title: str | None = None
    x: ChartAxis | None = None
    series: list[ChartSeries] = Field(default_factory=list)
    sampling: dict[str, Any] = Field(default_factory=lambda: {"applied": False, "method": None})
    annotations: list[dict[str, Any]] = Field(default_factory=list)


class MetricStatistics(BaseModel):
    sample_count: int = 0
    missing_rate: float = 0.0
    mean: float | None = None
    median: float | None = None
    min: float | None = None
    max: float | None = None
    min_time: str | None = None
    max_time: str | None = None
    std_dev: float | None = None
    cov: float | None = None
    delta: float | None = None
    delta_rate: float | None = None
