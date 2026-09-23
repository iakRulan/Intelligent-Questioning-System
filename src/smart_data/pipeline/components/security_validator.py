from src.smart_data.domain.query import QueryState
from src.smart_data.pipeline.components.sql_guard import SQLGuard
from src.smart_data.pipeline.reporter import StageReporter


class SecurityValidator:
    """阶段四：SQL 静态安全校验。开发阶段不做 Influx EXPLAIN。"""

    def __init__(self, guard: SQLGuard | None = None):
        self.guard = guard or SQLGuard()

    async def run(self, state: QueryState, reporter: StageReporter | None = None) -> QueryState:
        if reporter:
            await reporter.stage_started("security_validation")

        if not state.generated_sql or state.error_code or (state.intent and state.intent.needs_clarification):
            return state

        start_iso = end_iso = None
        if state.intent and state.intent.time_range:
            start_iso, end_iso = state.intent.time_range.as_utc_iso()

        allowed_tables = {metric.measurement.lower() for metric in state.metrics}
        allowed_columns = {metric.field.lower() for metric in state.metrics} | {"time", "asset_id"}
        allowed_assets = list(state.intent.asset_ids) if state.intent else []
        if state.allowed_asset_ids:
            allowed_assets = [asset for asset in allowed_assets if asset in state.allowed_asset_ids]

        is_safe, safe_sql, reasons = self.guard.validate_and_rewrite(
            raw_sql=state.generated_sql,
            start_time_iso=start_iso,
            end_time_iso=end_iso,
            allowed_tables=allowed_tables,
            allowed_columns=allowed_columns,
            allowed_assets=allowed_assets,
            enforce_whitelist=True,
        )

        if not is_safe:
            state.error_code = "SDQ-403-SQL-GUARD"
            state.error_message = "; ".join(reasons)
            state.security_passed = False
            if reporter:
                await reporter.stage_failed("security_validation", state.error_code, state.error_message)
            return state

        state.safe_sql = safe_sql
        state.security_passed = True
        state.warnings.extend(reasons)
        if reporter:
            await reporter.stage_completed(
                "security_validation",
                {
                    "safe_sql": state.safe_sql,
                    "checks_passed": ["ast_readonly", "time_window_enforced", "limit_enforced", "asset_scope_enforced"],
                    "warnings": reasons,
                },
            )
        return state
