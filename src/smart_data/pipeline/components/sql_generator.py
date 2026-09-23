from __future__ import annotations

import re
from datetime import datetime, timezone

from src.smart_data.config import settings
from src.smart_data.domain.errors import SQLSecurityError
from src.smart_data.domain.query import QueryState
from src.smart_data.pipeline.components.sql_guard import require_ident
from src.smart_data.pipeline.reporter import StageReporter
from src.smart_data.runtime import query_backend

_ASSET_SQL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")


class SQLGenerator:
    """阶段三：按当前查询后端生成只读 SQL。"""

    def __init__(self, prompt_version: str = "sql-gen-v1.0", model_version: str = "qwen-domain-v3"):
        self.prompt_version = prompt_version
        self.model_version = model_version

    def build_deterministic_sql(self, state: QueryState) -> str | None:
        if not state.metrics or not state.intent or not state.intent.time_range:
            return None

        dialect = _sql_dialect()
        primary_table = state.metrics[0].measurement
        table_metrics = [metric for metric in state.metrics if metric.measurement == primary_table]
        skipped = len(state.metrics) - len(table_metrics)
        if skipped:
            state.warnings.append(f"当前版本单次仅查询同一测量表，已忽略 {skipped} 个跨表指标")

        try:
            table = require_ident(primary_table)
            fields = [require_ident(metric.field) for metric in table_metrics]
        except SQLSecurityError:
            return None

        start_iso = _sql_time_literal(state.intent.time_range.start, dialect)
        end_iso = _sql_time_literal(state.intent.time_range.end, dialect)
        where_clauses = [f"time >= '{start_iso}'", f"time < '{end_iso}'"]

        assets = [asset for asset in state.intent.asset_ids if _ASSET_SQL_RE.match(asset)]
        if not assets:
            return None
        if len(assets) == 1:
            where_clauses.append(f"asset_id = '{assets[0]}'")
        else:
            in_list = ", ".join(f"'{asset}'" for asset in assets)
            where_clauses.append(f"asset_id IN ({in_list})")

        aggregation = _normalize_agg(state.intent.aggregation or table_metrics[0].aggregation)
        if state.intent.operation == "extreme" and aggregation is None:
            aggregation = "MAX"

        window_hours = (
            state.intent.time_range.end - state.intent.time_range.start
        ).total_seconds() / 3600.0
        group_by = list(state.intent.group_by)
        if window_hours > settings.query_policy.max_raw_window_hours and aggregation is None:
            aggregation = "AVG"
            state.warnings.append("原始时间窗超过策略上限，已自动改为按窗口聚合")

        include_asset = len(assets) > 1 or "asset" in group_by
        if aggregation:
            time_expr = _time_bucket_expr(dialect, window_hours, group_by)
            select_parts = [f"{time_expr} AS time"]
            if include_asset:
                select_parts.append("asset_id")
            for field in fields:
                select_parts.append(f"{aggregation}({field}) AS {field}")
            group_sql = f" GROUP BY {time_expr}"
            if include_asset:
                group_sql += ", asset_id"
        else:
            select_parts = ["time", "asset_id", *fields]
            group_sql = ""

        return (
            f"SELECT {', '.join(select_parts)} "
            f"FROM {table} "
            f"WHERE {' AND '.join(where_clauses)}"
            f"{group_sql} "
            f"ORDER BY time ASC"
        )

    async def run(self, state: QueryState, reporter: StageReporter | None = None) -> QueryState:
        dialect = _sql_dialect()
        if reporter:
            await reporter.stage_started("sql_generation", {"target_dialect": dialect})

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
                    "dialect": dialect,
                },
            )
        return state


def _sql_dialect() -> str:
    if query_backend in {"mysql", "influx"}:
        return query_backend
    if settings.pipeline.mode in {"mysql", "influx"}:
        return settings.pipeline.mode
    return "mysql"


def _sql_time_literal(value: datetime, dialect: str) -> str:
    moment = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    utc = moment.astimezone(timezone.utc)
    if dialect == "mysql":
        return utc.strftime("%Y-%m-%d %H:%M:%S")
    return utc.strftime("%Y-%m-%dT%H:%M:%SZ")


def _normalize_agg(value: str | None) -> str | None:
    if not value:
        return None
    mapping = {
        "avg": "AVG",
        "max": "MAX",
        "min": "MIN",
        "sum": "SUM",
        "count": "COUNT",
    }
    return mapping.get(value.lower())


def _time_bucket_expr(dialect: str, window_hours: float, group_by: list[str]) -> str:
    if dialect == "mysql":
        if "day" in group_by or window_hours > 24 * 31:
            seconds = 86400
        elif "hour" in group_by or window_hours > 24:
            seconds = 3600
        else:
            seconds = 300
        return f"FROM_UNIXTIME(FLOOR(UNIX_TIMESTAMP(time) / {seconds}) * {seconds})"

    if "hour" in group_by:
        interval = "1 hour"
    elif "day" in group_by:
        interval = "1 day"
    elif window_hours <= 24:
        interval = "5 minutes"
    elif window_hours <= 24 * 31:
        interval = "1 hour"
    else:
        interval = "1 day"
    return f"DATE_BIN(INTERVAL '{interval}', time, TIMESTAMP '1970-01-01 00:00:00Z')"
