from src.smart_data.domain.query import QueryState
from src.smart_data.pipeline.reporter import StageReporter


class SQLGenerator:
    """阶段三：SQL 生成组件。

    将已对齐的语义对象（资产、指标、时间戳、聚合方式）转化为针对 InfluxDB v3 的标准 SQL。
    """

    def __init__(self, prompt_version: str = "sql-gen-v1.0", model_version: str = "qwen-domain-v3"):
        self.prompt_version = prompt_version
        self.model_version = model_version

    def build_deterministic_sql(self, state: QueryState) -> str | None:
        """基于确定性规则构建高质量 InfluxDB v3 SQL。"""
        if not state.metrics or not state.intent or not state.intent.time_range:
            return None

        # 取主指标
        primary_metric = state.metrics[0]
        table = primary_metric.measurement
        field = primary_metric.field
        start_iso = state.intent.time_range.start.isoformat()
        end_iso = state.intent.time_range.end.isoformat()

        where_clauses = [
            f"time >= '{start_iso}'",
            f"time < '{end_iso}'",
        ]

        if state.intent.asset_ids:
            if len(state.intent.asset_ids) == 1:
                where_clauses.append(f"asset_id = '{state.intent.asset_ids[0]}'")
            else:
                assets_in = ", ".join(f"'{a}'" for a in state.intent.asset_ids)
                where_clauses.append(f"asset_id IN ({assets_in})")

        where_str = " AND ".join(where_clauses)

        # 聚合模式判断
        agg = state.intent.aggregation or primary_metric.aggregation
        if agg:
            agg_func = agg.upper()
            if agg_func not in ["AVG", "MAX", "MIN", "SUM", "COUNT"]:
                agg_func = "AVG"
            sql = (
                f"SELECT DATE_BIN(INTERVAL '1 hour', time, TIMESTAMP '1970-01-01 00:00:00Z') AS time, "
                f"{agg_func}({field}) AS {field} "
                f"FROM {table} "
                f"WHERE {where_str} "
                f"GROUP BY 1 "
                f"ORDER BY time ASC"
            )
        else:
            sql = (
                f"SELECT time, asset_id, {field} "
                f"FROM {table} "
                f"WHERE {where_str} "
                f"ORDER BY time ASC"
            )

        return sql

    async def run(self, state: QueryState, reporter: StageReporter | None = None) -> QueryState:
        if reporter:
            await reporter.stage_started("sql_generation", {"target_dialect": "InfluxDB_v3_SQL"})

        if not state.intent or state.intent.needs_clarification or state.error_code:
            return state

        sql = self.build_deterministic_sql(state)
        if not sql:
            state.error_code = "SDQ-422-SQL"
            state.error_message = "无法根据当前语义上下文生成合法的时序查询 SQL"
            if reporter:
                await reporter.stage_failed("sql_generation", state.error_code, state.error_message)
            return state

        state.generated_sql = sql
        state.prompt_versions["sql_generation"] = self.prompt_version
        state.model_version = self.model_version

        if reporter:
            await reporter.stage_completed(
                "sql_generation",
                {
                    "generated_sql": state.generated_sql,
                    "model_version": state.model_version,
                },
            )

        return state
