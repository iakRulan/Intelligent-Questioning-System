from datetime import datetime, timezone

import pytest

from src.smart_data.domain.intent import QueryIntent, TimeRange
from src.smart_data.domain.query import MetricRef, QueryState
from src.smart_data.pipeline.components.sql_generator import SQLGenerator


@pytest.mark.asyncio
async def test_sql_generator_builds_readonly_query():
    generator = SQLGenerator()
    state = QueryState(
        query_id="q1",
        trace_id="t1",
        user_id="u1",
        question="查询 GT-001 今天的平均排气温度",
        allowed_asset_ids=["GT-001"],
        intent=QueryIntent(
            operation="aggregate",
            asset_ids=["GT-001"],
            metric_terms=["排温"],
            aggregation="avg",
            time_range=TimeRange(
                start=datetime(2026, 9, 22, 0, 0, tzinfo=timezone.utc),
                end=datetime(2026, 9, 23, 0, 0, tzinfo=timezone.utc),
            ),
        ),
        metrics=[
            MetricRef(
                business_name="平均排气温度",
                point_code="T48_AVG",
                measurement="gt_telemetry",
                field="temperature",
                unit="℃",
                aggregation="avg",
            )
        ],
    )
    result = await generator.run(state)
    assert result.generated_sql is not None
    sql = result.generated_sql.upper()
    assert sql.startswith("SELECT")
    assert "GT_TELEMETRY" in sql
    assert "TEMPERATURE" in sql
    assert "GT-001" in result.generated_sql
    assert "INSERT" not in sql
    assert "SELECT *" not in result.generated_sql.upper()
