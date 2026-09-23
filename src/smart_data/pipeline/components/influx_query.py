from __future__ import annotations

import math
import random
import time
from datetime import datetime, timedelta, timezone
from typing import Any

from src.smart_data.config import settings
from src.smart_data.domain.query import QueryState
from src.smart_data.pipeline.reporter import StageReporter
from src.smart_data.runtime import query_backend


class InfluxQueryExecutor:
    """阶段五：按运行后端执行只读查询（MySQL / InfluxDB / mock）。"""

    def __init__(self, simulate_fallback: bool = False):
        self.simulate_fallback = simulate_fallback

    def _generate_simulated_gt_series(
        self,
        field: str,
        asset_id: str,
        start_time: datetime,
        end_time: datetime,
        seed: str,
        max_rows: int,
    ) -> tuple[list[dict[str, Any]], bool]:
        if start_time >= end_time:
            return [], False

        rng = random.Random(seed)
        total_minutes = max((end_time - start_time).total_seconds() / 60.0, 1.0)
        natural_step = 5.0 if total_minutes <= 24 * 60 else 60.0
        natural_points = total_minutes / natural_step
        truncated = natural_points > max_rows
        step_minutes = max(natural_step, math.ceil(total_minutes / max_rows))

        if "temp" in field:
            base_val, amplitude = 520.0, 15.0
        elif "speed" in field:
            base_val, amplitude = 9400.0, 150.0
        elif "power" in field:
            base_val, amplitude = 26.5, 2.0
        elif "eff" in field:
            base_val, amplitude = 34.0, 1.5
        else:
            base_val, amplitude = 25.0, 5.0

        records: list[dict[str, Any]] = []
        current = start_time
        index = 0
        while current < end_time and len(records) < max_rows:
            value = base_val + amplitude * math.sin(index * 0.2) + rng.uniform(-1.0, 1.0)
            records.append(
                {
                    "time": current.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "asset_id": asset_id,
                    field: round(value, 2),
                }
            )
            current += timedelta(minutes=step_minutes)
            index += 1
        return records, truncated

    async def run(self, state: QueryState, reporter: StageReporter | None = None) -> QueryState:
        if reporter:
            await reporter.stage_started("data_query", {"backend": query_backend})

        if not state.safe_sql or state.error_code or (state.intent and state.intent.needs_clarification):
            return state

        started = time.perf_counter()
        try:
            if query_backend == "mysql":
                from src.smart_data.infrastructure.mysql.query import execute_readonly_sql

                columns, records = execute_readonly_sql(state.safe_sql)
                truncated = len(records) >= settings.query_policy.max_result_rows
            elif query_backend == "influx":
                from src.smart_data.infrastructure.influx.client import InfluxHttpClient

                raw_records = InfluxHttpClient().query(state.safe_sql)
                records = [_normalize_influx_row(item) for item in raw_records]
                columns = list(records[0].keys()) if records else []
                truncated = len(records) >= settings.query_policy.max_result_rows
                if len(records) > settings.query_policy.max_result_rows:
                    records = records[: settings.query_policy.max_result_rows]
            else:
                records, truncated, columns = self._mock_records(state)
        except TimeoutError:
            state.error_code = "SDQ-504-INFLUX-TIMEOUT"
            state.error_message = "时序查询超时"
            if reporter:
                await reporter.stage_failed("data_query", state.error_code, state.error_message)
            return state
        except Exception as exc:
            message = str(exc)
            if "timeout" in message.lower():
                state.error_code = "SDQ-504-INFLUX-TIMEOUT"
                state.error_message = "时序查询超时"
            else:
                state.error_code = "SDQ-503-DATASOURCE"
                state.error_message = f"数据源查询失败: {message[:240]}"
            if reporter:
                await reporter.stage_failed("data_query", state.error_code, state.error_message)
            return state

        state.raw_columns = columns
        state.raw_records = records
        state.row_count = len(records)
        state.truncated = truncated
        state.query_elapsed_ms = round((time.perf_counter() - started) * 1000.0, 2)
        if reporter:
            await reporter.stage_completed(
                "data_query",
                {
                    "backend": query_backend,
                    "row_count": state.row_count,
                    "elapsed_ms": state.query_elapsed_ms,
                    "truncated": state.truncated,
                    "columns": state.raw_columns,
                    "preview_rows": state.raw_records[:5],
                },
            )
        return state

    def _mock_records(self, state: QueryState) -> tuple[list[dict[str, Any]], bool, list[str]]:
        primary_metric = state.metrics[0] if state.metrics else None
        field = primary_metric.field if primary_metric else "value"
        asset_id = state.intent.asset_ids[0] if state.intent and state.intent.asset_ids else "GT-001"
        start_time = (
            state.intent.time_range.start
            if state.intent and state.intent.time_range
            else datetime.now(timezone.utc) - timedelta(days=1)
        )
        end_time = (
            state.intent.time_range.end if state.intent and state.intent.time_range else datetime.now(timezone.utc)
        )
        records, truncated = self._generate_simulated_gt_series(
            field=field,
            asset_id=asset_id,
            start_time=start_time,
            end_time=end_time,
            seed=state.query_id,
            max_rows=settings.query_policy.max_result_rows,
        )
        extra_fields = []
        if primary_metric:
            extra_fields = [
                metric.field
                for metric in state.metrics[1:]
                if metric.measurement == primary_metric.measurement and metric.field != field
            ]
            rng = random.Random(state.query_id + ":extra")
            for record in records:
                for extra in extra_fields:
                    record[extra] = round(float(record[field]) * 0.02 + rng.uniform(-0.2, 0.2), 2)
        columns = ["time", "asset_id", field, *extra_fields]
        return records, truncated, columns


def _normalize_influx_row(row: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(row)
    if "time" in normalized:
        value = normalized["time"]
        if isinstance(value, datetime):
            normalized["time"] = value.isoformat().replace("+00:00", "Z")
    return normalized
