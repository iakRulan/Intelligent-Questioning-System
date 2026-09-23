from __future__ import annotations

import logging
import re
from datetime import datetime, timezone

from src.smart_data.config import settings
from src.smart_data.domain.errors import SQLSecurityError
from src.smart_data.domain.query import QueryState
from src.smart_data.pipeline.components.sql_guard import require_ident
from src.smart_data.pipeline.reporter import StageReporter
from src.smart_data.runtime import query_backend

_ASSET_SQL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
logger = logging.getLogger(__name__)


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

        llm_used = False
        sql = await self._llm_sql(state, dialect)
        if sql:
            llm_used = True
            state.prompt_versions["sql_generation"] = "sql_generation-v1"
            state.model_version = settings.llm.model
        else:
            sql = self.build_deterministic_sql(state)
            state.prompt_versions.setdefault("sql_generation", self.prompt_version)
            if not state.model_version:
                state.model_version = settings.llm.model if llm_used else self.model_version
        if not sql:
            state.error_code = "SDQ-422-SQL"
            state.error_message = "无法根据当前语义上下文生成合法的时序查询 SQL"
            if reporter:
                await reporter.stage_failed("sql_generation", state.error_code, state.error_message)
            return state

        state.generated_sql = sql
        if reporter:
            await reporter.stage_completed(
                "sql_generation",
                {
                    "generated_sql": state.generated_sql,
                    "model_version": state.model_version,
                    "dialect": dialect,
                    "llm_used": llm_used,
                },
            )
        return state

    async def _llm_sql(self, state: QueryState, dialect: str) -> str | None:
        from src.smart_data.infrastructure.llm import LLMError, get_llm_client
        from src.smart_data.infrastructure.llm.prompts import load_prompt
        from src.smart_data.pipeline.components.sql_guard import SQLGuard

        client = get_llm_client()
        if client is None or not state.intent or not state.intent.time_range:
            return None

        start_iso = _sql_time_literal(state.intent.time_range.start, dialect)
        end_iso = _sql_time_literal(state.intent.time_range.end, dialect)
        metric_lines = "\n".join(
            f"- {item.business_name} / {item.point_code} / 表 {item.measurement} / 字段 {item.field} / 单位 {item.unit or ''}"
            for item in state.metrics
        )
        user_prompt = (
            f"方言: {dialect}\n"
            f"授权机组: {', '.join(state.intent.asset_ids)}\n"
            f"机组过滤字段必须使用 asset_id，禁止 unit_id。\n"
            f"时间范围 UTC 左闭右开: {start_iso} ~ {end_iso}\n"
            f"聚合: {state.intent.aggregation or '无'}\n"
            f"分组: {', '.join(state.intent.group_by) or '无'}\n"
            f"已映射指标:\n{metric_lines}\n"
            f"用户问题: {state.question}\n"
            "结果要给趋势图使用：SELECT 必须包含 time 列；聚合时按时间桶 GROUP BY time，不要只返回一个标量。\n"
            '只输出 JSON，keys 为 sql、reasoning_summary、used_metrics、assumptions。'
        )
        messages = [
            {"role": "system", "content": load_prompt("sql_generation")},
            {"role": "user", "content": user_prompt},
        ]
        allowed_tables = {item.measurement.lower() for item in state.metrics}
        allowed_columns = {item.field.lower() for item in state.metrics} | {"time", "asset_id"}
        guard = SQLGuard()
        last_sql = None
        last_reasons: list[str] = []
        attempts = settings.query_policy.max_auto_repair_attempts + 1
        for index in range(attempts):
            try:
                if index > 0:
                    messages.append({"role": "assistant", "content": last_sql or ""})
                    messages.append(
                        {
                            "role": "user",
                            "content": "上一版 SQL 未通过安全校验：" + "; ".join(last_reasons) + "。请只输出修正后的 JSON。",
                        }
                    )
                payload = await client.chat_json(
                    messages,
                    temperature=0.0,
                    max_tokens=1536,
                    trace_id=state.trace_id,
                    operation="sql_generation",
                )
            except (LLMError, Exception) as exc:
                logger.warning("SQL 模型生成失败: %s", exc)
                return None
            candidate = str(payload.get("sql") or "").strip()
            if not candidate:
                return None
            last_sql = candidate
            ok, _safe, reasons = guard.validate_and_rewrite(
                candidate,
                start_time_iso=start_iso,
                end_time_iso=end_iso,
                allowed_tables=allowed_tables,
                allowed_columns=allowed_columns,
                allowed_assets=list(state.intent.asset_ids),
                enforce_whitelist=True,
            )
            if ok:
                return candidate
            last_reasons = reasons
        return None


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
