import pytest

from src.smart_data.domain.query import QueryState
from src.smart_data.pipeline.components.trend_analyzer import TrendAnalyzer
from src.smart_data.pipeline.reporter import StageReporter


def test_median_even_count():
    analyzer = TrendAnalyzer()
    records = [{"temperature": 1.0}, {"temperature": 2.0}, {"temperature": 3.0}, {"temperature": 4.0}]
    stats = analyzer.calculate_statistics(records, "temperature")
    assert stats.median == 2.5
    assert stats.sample_count == 4


def test_resolve_aliased_numeric_field():
    from src.smart_data.domain.query import MetricRef, QueryState
    from src.smart_data.pipeline.components.trend_analyzer import TrendAnalyzer, _resolve_value_field

    state = QueryState(
        query_id="q",
        trace_id="t",
        user_id="u",
        question="q",
        metrics=[
            MetricRef(
                business_name="平均排气温度",
                point_code="T48_AVG",
                measurement="gt_telemetry",
                field="temperature",
                unit="℃",
            )
        ],
        raw_records=[{"time": "2026-09-23T00:00:00Z", "avg_t48": 520.1}],
        row_count=1,
    )
    assert _resolve_value_field(state) == "avg_t48"
    stats = TrendAnalyzer().calculate_statistics(state.raw_records, "avg_t48")
    assert stats.mean == 520.1


def test_empty_statistics():
    analyzer = TrendAnalyzer()
    stats = analyzer.calculate_statistics([], "temperature")
    assert stats.sample_count == 0
    assert stats.mean is None


@pytest.mark.asyncio
async def test_empty_result_still_completes():
    analyzer = TrendAnalyzer()
    state = QueryState(
        query_id="q-empty",
        trace_id="t-empty",
        user_id="u1",
        question="查询 GT-001 今天的排温",
        raw_records=[],
        row_count=0,
    )
    reporter = StageReporter(query_id="q-empty", trace_id="t-empty")
    result = await analyzer.run(state, reporter)
    assert result.conclusion is not None
    assert "样本数为 0" in result.conclusion

    events = []
    while not reporter.queue.empty():
        events.append(reporter.queue.get_nowait())
    names = [event.event for event in events if event is not None]
    assert "result.completed" in names
