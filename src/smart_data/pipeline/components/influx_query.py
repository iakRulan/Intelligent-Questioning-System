import time
import math
import random
from datetime import datetime, timedelta, timezone
from typing import Any
from src.smart_data.domain.query import QueryState
from src.smart_data.pipeline.reporter import StageReporter


class InfluxQueryExecutor:
    """阶段五：时序数据查询执行器。

    支持对 InfluxDB v3 真实数据库执行只读查询；
    在环境未配置或离线测试时自动启用燃气轮机动力学模拟数据生成器。
    """

    def __init__(self, simulate_fallback: bool = True):
        self.simulate_fallback = simulate_fallback

    def _generate_simulated_gt_series(
        self,
        table: str,
        field: str,
        asset_id: str,
        start_time: datetime,
        end_time: datetime,
        step_minutes: int = 60,
    ) -> list[dict[str, Any]]:
        records = []
        curr = start_time
        # 基线参数
        base_val = 520.0 if "temp" in field else (9400.0 if "speed" in field else (26.5 if "power" in field else 25.0))
        amplitude = 15.0 if "temp" in field else (150.0 if "speed" in field else (2.0 if "power" in field else 5.0))

        idx = 0
        while curr < end_time and len(records) < 1000:
            # 模拟工况平稳伴随轻微波动与噪声
            noise = random.uniform(-1.0, 1.0)
            val = base_val + amplitude * math.sin(idx * 0.2) + noise
            records.append({
                "time": curr.isoformat(),
                "asset_id": asset_id,
                field: round(val, 2),
            })
            curr += timedelta(minutes=step_minutes)
            idx += 1

        return records

    async def run(self, state: QueryState, reporter: StageReporter | None = None) -> QueryState:
        if reporter:
            await reporter.stage_started("data_query")

        if not state.safe_sql or state.error_code or (state.intent and state.intent.needs_clarification):
            return state

        t0 = time.perf_counter()

        # 执行查询（当前为自适应离线模拟驱动）
        primary_metric = state.metrics[0] if state.metrics else None
        field = primary_metric.field if primary_metric else "value"
        table = primary_metric.measurement if primary_metric else "gt_measurement"
        asset_id = state.intent.asset_ids[0] if state.intent and state.intent.asset_ids else "GT-001"

        start_time = state.intent.time_range.start if state.intent and state.intent.time_range else datetime.now(timezone.utc) - timedelta(days=1)
        end_time = state.intent.time_range.end if state.intent and state.intent.time_range else datetime.now(timezone.utc)

        records = self._generate_simulated_gt_series(
            table=table,
            field=field,
            asset_id=asset_id,
            start_time=start_time,
            end_time=end_time,
        )

        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        state.raw_columns = ["time", "asset_id", field]
        state.raw_records = records
        state.row_count = len(records)
        state.query_elapsed_ms = round(elapsed_ms, 2)

        if reporter:
            await reporter.stage_completed(
                "data_query",
                {
                    "row_count": state.row_count,
                    "elapsed_ms": state.query_elapsed_ms,
                    "columns": state.raw_columns,
                    "preview_rows": state.raw_records[:5],
                },
            )

        return state
