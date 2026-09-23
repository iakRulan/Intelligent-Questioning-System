import asyncio
import logging

from src.smart_data.domain.query import QueryState
from src.smart_data.pipeline.components import (
    InfluxQueryExecutor,
    IntentParser,
    SecurityValidator,
    SemanticMapper,
    SQLGenerator,
    TrendAnalyzer,
)
from src.smart_data.pipeline.reporter import StageReporter

logger = logging.getLogger(__name__)


class QueryPipelineRunner:
    """智能问数六步流式执行器。"""

    def __init__(
        self,
        intent_parser: IntentParser | None = None,
        semantic_mapper: SemanticMapper | None = None,
        sql_generator: SQLGenerator | None = None,
        security_validator: SecurityValidator | None = None,
        influx_executor: InfluxQueryExecutor | None = None,
        trend_analyzer: TrendAnalyzer | None = None,
    ):
        self.intent_parser = intent_parser or IntentParser()
        self.semantic_mapper = semantic_mapper or SemanticMapper()
        self.sql_generator = sql_generator or SQLGenerator()
        self.security_validator = security_validator or SecurityValidator()
        self.influx_executor = influx_executor or InfluxQueryExecutor()
        self.trend_analyzer = trend_analyzer or TrendAnalyzer()

    async def execute(self, state: QueryState, reporter: StageReporter) -> QueryState:
        try:
            await reporter.accepted()

            state = await self.intent_parser.run(state, reporter)
            if _should_stop(state):
                await reporter.stream_end()
                return state

            state = await self.semantic_mapper.run(state, reporter)
            if _should_stop(state):
                await reporter.stream_end()
                return state

            state = await self.sql_generator.run(state, reporter)
            if _should_stop(state):
                await reporter.stream_end()
                return state

            state = await self.security_validator.run(state, reporter)
            if _should_stop(state):
                await reporter.stream_end()
                return state

            state = await self.influx_executor.run(state, reporter)
            if _should_stop(state):
                await reporter.stream_end()
                return state

            state = await self.trend_analyzer.run(state, reporter)
            await reporter.stream_end()
            return state

        except asyncio.CancelledError:
            logger.info("Pipeline task cancelled by client: %s", state.query_id)
            state.cancelled = True
            await reporter.query_cancelled("Client disconnected")
            await reporter.stream_end()
            raise
        except Exception as exc:
            logger.exception("Unexpected error in pipeline: %s", exc)
            state.error_code = "SDQ-500-INTERNAL"
            state.error_message = str(exc)
            await reporter.stage_failed("pipeline", state.error_code, str(exc))
            await reporter.stream_end()
            return state


def _should_stop(state: QueryState) -> bool:
    if state.error_code:
        return True
    return bool(state.intent and state.intent.needs_clarification)
