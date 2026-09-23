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
