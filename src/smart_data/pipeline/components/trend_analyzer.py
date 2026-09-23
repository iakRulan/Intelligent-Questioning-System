import math
from typing import Any

from src.smart_data.config import settings
from src.smart_data.domain.query import QueryState
from src.smart_data.domain.result import ChartAxis, ChartDSL, ChartSeries, MetricStatistics
from src.smart_data.pipeline.reporter import StageReporter


class TrendAnalyzer:
    """阶段六：确定性统计、图表 DSL 与受控结论。空结果视为正常业务结果。"""

    def calculate_statistics(self, records: list[dict[str, Any]], field: str) -> MetricStatistics:
        if not records:
            return MetricStatistics()

        values = [row[field] for row in records if field in row and isinstance(row[field], (int, float))]
        sample_count = len(values)
        missing_rate = 1.0 - (sample_count / len(records)) if records else 0.0
        if not values:
            return MetricStatistics(sample_count=0, missing_rate=round(missing_rate, 4))

        average = sum(values) / sample_count
        ordered = sorted(values)
        if sample_count % 2 == 1:
            median = ordered[sample_count // 2]
        else:
            median = (ordered[sample_count // 2 - 1] + ordered[sample_count // 2]) / 2
        min_val = min(values)
        max_val = max(values)

        min_time = next((row.get("time") for row in records if row.get(field) == min_val), None)
        max_time = next((row.get("time") for row in records if row.get(field) == max_val), None)

        variance = sum((item - average) ** 2 for item in values) / (sample_count - 1 if sample_count > 1 else 1)
        std_dev = math.sqrt(variance)
        cov = (std_dev / average) if average != 0 else 0.0
        delta = values[-1] - values[0]
        delta_rate = (delta / values[0] * 100.0) if values[0] != 0 else 0.0

        return MetricStatistics(
            sample_count=sample_count,
            missing_rate=round(missing_rate, 4),
            mean=round(average, 2),
            median=round(median, 2),
            min=round(min_val, 2),
            max=round(max_val, 2),
            min_time=min_time,
            max_time=max_time,
            std_dev=round(std_dev, 2),
            cov=round(cov, 4),
            delta=round(delta, 2),
            delta_rate=round(delta_rate, 2),
        )

    def generate_chart_dsl(self, state: QueryState, field: str) -> ChartDSL:
        primary_metric = state.metrics[0] if state.metrics else None
        metric_name = primary_metric.business_name if primary_metric else "时序指标"
        unit = primary_metric.unit if primary_metric else ""
        asset_label = state.intent.asset_ids[0] if state.intent and state.intent.asset_ids else ""

        chart_type = "line"
        if state.row_count <= 1:
            chart_type = "kpi"
        elif state.intent and state.intent.aggregation in {"sum", "count"}:
            chart_type = "bar"
        elif state.intent and state.intent.operation == "correlation":
            chart_type = "scatter"

        return ChartDSL(
            type=chart_type,
            title=f"{asset_label} {metric_name} 运行趋势".strip(),
            x=ChartAxis(field="time", type="time"),
            series=[
                ChartSeries(field=field, name=metric_name, unit=unit, chart_type=chart_type)
            ],
            sampling={"applied": state.truncated, "method": "server_downsample" if state.truncated else None},
        )

    async def _llm_conclusion(
        self, state: QueryState, stats: MetricStatistics, metric_name: str, unit: str
    ) -> str | None:
        from src.smart_data.infrastructure.llm import LLMError, get_llm_client
        from src.smart_data.infrastructure.llm.prompts import load_prompt

        client = get_llm_client()
        if client is None:
            return None
        summary = {
            "assets": state.intent.asset_ids if state.intent else [],
            "metric": metric_name,
            "unit": unit,
            "statistics": stats.model_dump(),
            "row_count": state.row_count,
        }
        try:
            payload = await client.chat_json(
                [
                    {"role": "system", "content": load_prompt("conclusion")},
                    {"role": "user", "content": f"统计摘要：{summary}"},
                ],
                temperature=0.15,
                max_tokens=1024,
                trace_id=state.trace_id,
                operation="conclusion",
            )
        except (LLMError, Exception):
            return None
        text = str(payload.get("conclusion") or "").strip()
        return text or None

    def generate_conclusion(self, state: QueryState, stats: MetricStatistics, metric_name: str, unit: str) -> str:
        asset_str = "、".join(state.intent.asset_ids) if state.intent and state.intent.asset_ids else "目标机组"
        if stats.sample_count == 0:
            return (
                "【数据事实】本次查询未返回任何数据点，统计样本数为 0。\n"
                "【说明】空结果是正常业务结果，不代表设备异常。如需判断工况偏离，请进入异常检测模块。"
            )
        return "\n".join(
            [
                "【数据事实】",
                f"1. {asset_str} 的{metric_name}有效样本 {stats.sample_count} 个，缺失率 {stats.missing_rate}。",
                f"2. 均值 {stats.mean}{unit}，中位数 {stats.median}{unit}，最低 {stats.min}{unit}（{stats.min_time or '未知'}），最高 {stats.max}{unit}（{stats.max_time or '未知'}）。",
                f"3. 标准差 {stats.std_dev}，变异系数 {stats.cov}，区间首尾变化 {stats.delta}{unit}（{stats.delta_rate}%）。",
                "【说明】",
                "以上内容仅为统计描述，不构成故障诊断结论。如发现残差突增或越限，建议进入异常检测模块继续分析。",
            ]
        )

    async def run(self, state: QueryState, reporter: StageReporter | None = None) -> QueryState:
        if reporter:
            await reporter.stage_started("trend_analysis")

        if state.error_code or (state.intent and state.intent.needs_clarification):
            return state

        primary_metric = state.metrics[0] if state.metrics else None
        field = _resolve_value_field(state)
        metric_name = primary_metric.business_name if primary_metric else "时序指标"
        unit = primary_metric.unit if primary_metric else ""

        stats = self.calculate_statistics(state.raw_records, field)
        chart = self.generate_chart_dsl(state, field)
        conclusion = await self._llm_conclusion(state, stats, metric_name, unit)
        if not conclusion:
            conclusion = self.generate_conclusion(state, stats, metric_name, unit)

        state.statistics = stats.model_dump()
        state.visualization = chart.model_dump()
        state.conclusion = conclusion

        if reporter:
            sql_payload = {
                "text": state.safe_sql or state.generated_sql if state.include_sql else None,
                "visible": state.include_sql,
            }
            preview_rows = state.raw_preview_rows if state.include_raw_preview else 0
            payload = {
                "intent": state.intent.model_dump(mode="json") if state.intent else {},
                "mapping": {
                    "assets": state.intent.asset_ids if state.intent else [],
                    "metrics": [metric.point_code for metric in state.metrics],
                    "dictionary_version": state.dictionary_version,
                },
                "sql": sql_payload,
                "query": {
                    "row_count": state.row_count,
                    "truncated": state.truncated,
                    "elapsed_ms": state.query_elapsed_ms,
                    "columns": state.raw_columns,
                },
                "statistics": state.statistics,
                "visualization": state.visualization,
                "conclusion": state.conclusion,
                "raw_preview": state.raw_records[:preview_rows],
                "warnings": state.warnings,
                "versions": {
                    "model": state.model_version or settings.llm.model,
                    "prompt": state.prompt_versions.get("sql_generation", "sql-generation-v1"),
                    "pipeline": settings.pipeline.version,
                    "dictionary": state.dictionary_version,
                },
            }
            await reporter.stage_completed("trend_analysis", {"statistics": state.statistics, "row_count": state.row_count})
            await reporter.result_completed(payload)
        return state


def _resolve_value_field(state: QueryState) -> str:
    preferred = state.metrics[0].field if state.metrics else "value"
    if not state.raw_records:
        return preferred
    row = state.raw_records[0]
    if preferred in row:
        return preferred
    for key, value in row.items():
        if key not in {"time", "asset_id"} and isinstance(value, (int, float)):
            return key
    return preferred
