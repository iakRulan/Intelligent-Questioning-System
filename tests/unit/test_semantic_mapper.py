import pytest
from src.smart_data.pipeline.components.semantic_mapper import SemanticMapper
from src.smart_data.domain.query import QueryState
from src.smart_data.domain.intent import QueryIntent, TimeRange
from datetime import datetime, timezone


@pytest.mark.asyncio
async def test_semantic_mapper_maps_alias_successfully():
    mapper = SemanticMapper()
    state = QueryState(
        query_id="q1",
        trace_id="t1",
        user_id="u1",
        question="查询 GT-001 今天的排温",
        allowed_asset_ids=["GT-001"],
        intent=QueryIntent(
            operation="query",
            asset_ids=["GT-001"],
            metric_terms=["排温"],
            time_range=TimeRange(
                start=datetime(2026, 9, 22, 0, 0, tzinfo=timezone.utc),
                end=datetime(2026, 9, 23, 0, 0, tzinfo=timezone.utc),
            ),
        ),
    )
    res_state = await mapper.run(state)
    assert len(res_state.metrics) == 1
    assert res_state.metrics[0].point_code == "T48_AVG"
    assert res_state.metrics[0].measurement == "gt_telemetry"
    assert res_state.metrics[0].field == "temperature"


@pytest.mark.asyncio
async def test_semantic_mapper_intercepts_unauthorized_asset():
    mapper = SemanticMapper()
    state = QueryState(
        query_id="q2",
        trace_id="t2",
        user_id="u1",
        question="查询 GT-002 的排温",
        allowed_asset_ids=["GT-001"],  # 仅有 GT-001 权限
        intent=QueryIntent(
            operation="query",
            asset_ids=["GT-002"],
            metric_terms=["排温"],
            time_range=TimeRange(
                start=datetime(2026, 9, 22, 0, 0, tzinfo=timezone.utc),
                end=datetime(2026, 9, 23, 0, 0, tzinfo=timezone.utc),
            ),
        ),
    )
    res_state = await mapper.run(state)
    assert res_state.error_code == "SDQ-403-SCOPE"
    assert "无权限访问" in (res_state.error_message or "")


@pytest.mark.asyncio
async def test_semantic_mapper_triggers_clarification_on_ambiguous_term():
    mapper = SemanticMapper()
    state = QueryState(
        query_id="q3",
        trace_id="t3",
        user_id="u1",
        question="查询 GT-001 今天的效率",
        allowed_asset_ids=["GT-001"],
        intent=QueryIntent(
            operation="query",
            asset_ids=["GT-001"],
            metric_terms=["效率"],
            time_range=TimeRange(
                start=datetime(2026, 9, 22, 0, 0, tzinfo=timezone.utc),
                end=datetime(2026, 9, 23, 0, 0, tzinfo=timezone.utc),
            ),
        ),
    )
    res_state = await mapper.run(state)
    assert res_state.intent is not None
    assert res_state.intent.needs_clarification is True
