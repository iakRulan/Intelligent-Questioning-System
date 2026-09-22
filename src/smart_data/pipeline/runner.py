import asyncio
import logging
from src.smart_data.domain.query import QueryState
from src.smart_data.pipeline.reporter import StageReporter
from src.smart_data.pipeline.components import (
    IntentParser,
    SemanticMapper,
    SQLGenerator,
    SecurityValidator,
    InfluxQueryExecutor,
    TrendAnalyzer,
)

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
            # 0. 受理请求
            await reporter.accepted()

            # 1. 意图解析
            state = await self.intent_parser.run(state, reporter)
            if state.intent and state.intent.needs_clarification:
                await reporter.stream_end()
                return state
            if state.error_code:
                await reporter.stream_end()
                return state

            # 2. 语义映射
            state = await self.semantic_mapper.run(state, reporter)
            if state.intent and state.intent.needs_clarification:
                await reporter.stream_end()
                return state
            if state.error_code:
                await reporter.stream_end()
                return state

            # 3. SQL 生成
            state = await self.sql_generator.run(state, reporter)
            if state.error_code:
                await reporter.stream_end()
                return state

            # 4. 安全校验
            state = await self.security_validator.run(state, reporter)
            if state.error_code:
                await reporter.stream_end()
                return state

            # 5. 数据查询
            state = await self.influx_executor.run(state, reporter)
            if state.error_code:
                await reporter.stream_end()
                return state

            # 6. 趋势分析与图表生成
            state = await self.trend_analyzer.run(state, reporter)

            # 流正常结束
            await reporter.stream_end()
            return state

        except asyncio.CancelledError:
            logger.info("Pipeline task cancelled by client: %s", state.query_id)
            state.cancelled = True
            await reporter.query_cancelled("Client disconnected")
            await reporter.stream_end()
            raise
        except Exception as e:
            logger.exception("Unexpected error in pipeline: %s", str(e))
            state.error_code = "SDQ-500-INTERNAL"
            state.error_message = str(e)
            await reporter.stage_failed("pipeline", state.error_code, str(e))
            await reporter.stream_end()
            return state
