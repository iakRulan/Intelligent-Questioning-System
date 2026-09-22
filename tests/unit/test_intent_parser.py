import pytest
from datetime import datetime, timezone
from src.smart_data.pipeline.components.intent_parser import IntentParser
from src.smart_data.domain.query import QueryState


def test_parse_assets_and_metrics():
    parser = IntentParser()
    assets = parser.extract_assets("帮我查一下 #1 机组和 2号机的排气温度与转速")
    assert "GT-001" in assets
    assert "GT-002" in assets

    metrics = parser.extract_metrics("帮我查一下 #1 机组和 2号机的排气温度与转速")
    assert "排气温度" in metrics
    assert "转速" in metrics


def test_parse_natural_relative_time():
    parser = IntentParser()
    ref_time = datetime(2026, 9, 23, 10, 0, 0, tzinfo=timezone.utc)
    
    tr_today, is_amb = parser.parse_time_range("查询今天的排温", ref_time=ref_time)
    assert not is_amb
    assert tr_today is not None
    assert tr_today.start < tr_today.end

    tr_last_week, _ = parser.parse_time_range("统计上周排温均值", ref_time=ref_time)
    assert tr_last_week is not None
    assert (tr_last_week.end - tr_last_week.start).days == 7


def test_ambiguous_time_triggers_clarification():
    parser = IntentParser()
    _, is_amb = parser.parse_time_range("查一下最近的排温")
    assert is_amb is True


@pytest.mark.asyncio
async def test_intent_parser_needs_clarification_when_no_time():
    parser = IntentParser()
    state = QueryState(
        query_id="q1",
        trace_id="t1",
        user_id="u1",
        question="查一下排温",
        allowed_asset_ids=["GT-001"],
    )
    res_state = await parser.run(state)
    assert res_state.intent is not None
    assert res_state.intent.needs_clarification is True
    assert len(res_state.intent.clarification_questions) > 0
