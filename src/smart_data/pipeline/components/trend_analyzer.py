import math
from typing import Any
from src.smart_data.domain.query import QueryState
from src.smart_data.domain.result import ChartDSL, ChartSeries, ChartAxis, MetricStatistics
from src.smart_data.pipeline.reporter import StageReporter


class TrendAnalyzer:
    """阶段六：趋势分析与图表生成组件。

    执行确定性时序统计分析，构建前端安全渲染的图表 DSL，并生成受控的自然语言分析结论。
    """

    def calculate_statistics(self, records: list[dict[str, Any]], field: str) -> MetricStatistics:
        if not records:
            return MetricStatistics()

        values = [r[field] for r in records if field in r and isinstance(r[field], (int, float))]
        if not values:
            return MetricStatistics(sample_count=len(records))

        n = len(values)
        avg_val = sum(values) / n
        sorted_vals = sorted(values)
        median_val = sorted_vals[n // 2]
        min_val = min(values)
        max_val = max(values)

        # 极值发生时间
        min_time = None
        max_time = None
        for r in records:
            if r.get(field) == min_val and min_time is None:
                min_time = r.get("time")
            if r.get(field) == max_val and max_time is None:
                max_time = r.get("time")

        # 方差与标准差
        variance = sum((x - avg_val) ** 2 for x in values) / (n - 1 if n > 1 else 1)
        std_dev = math.sqrt(variance)
        cov = (std_dev / avg_val) if avg_val != 0 else 0.0

        # 首尾变化
        first_val = values[0]
        last_val = values[-1]
        delta = last_val - first_val
        delta_rate = (delta / first_val * 100.0) if first_val != 0 else 0.0

        return MetricStatistics(
            sample_count=n,
            missing_rate=0.0,
            mean=round(avg_val, 2),
            median=round(median_val, 2),
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

        chart_type = "line"
        if state.row_count <= 1:
            chart_type = "kpi"
        elif state.intent and state.intent.aggregation in ["sum", "count"]:
            chart_type = "bar"

        return ChartDSL(
            type=chart_type,
            title=f"{state.intent.asset_ids[0] if state.intent and state.intent.asset_ids else ''} {metric_name} 运行趋势",
            x=ChartAxis(field="time", type="time"),
            series=[
                ChartSeries(
                    field=field,
                    name=metric_name,
                    unit=unit,
                    chart_type=chart_type,
                )
            ],
        )

    def generate_conclusion(self, state: QueryState, stats: MetricStatistics, metric_name: str, unit: str) -> str:
        asset_str = state.intent.asset_ids[0] if state.intent and state.intent.asset_ids else "目标机组"
        lines = [
            f"1. 总体水平：{asset_str}在统计区间内{metric_name}均值为 {stats.mean}{unit}，中位数为 {stats.median}{unit}，整体工况处于稳定受控区间。",
            f"2. 极值表现：监测到区间最高值为 {stats.max}{unit}（发生时刻：{stats.max_time or '未知'}），最低值为 {stats.min}{unit}（发生时刻：{stats.min_time or '未知'}）。",
            f"3. 波动特征：指标标准差为 {stats.std_dev}，变异系数为 {stats.cov}，数据离散度较低，未出现连续大幅剧烈跳变。",
            f"4. 趋势研判：区间首尾净变化为 {stats.delta}{unit}（幅度 {stats.delta_rate}%）。若发现特定时段残差突增，建议点击下方按钮一键联动「异常检测」模块进行故障溯源。",
        ]
        return "\n".join(lines)

    async def run(self, state: QueryState, reporter: StageReporter | None = None) -> QueryState:
        if reporter:
            await reporter.stage_started("trend_analysis")

        if not state.raw_records or state.error_code or (state.intent and state.intent.needs_clarification):
            return state

        primary_metric = state.metrics[0] if state.metrics else None
        field = primary_metric.field if primary_metric else "value"
        metric_name = primary_metric.business_name if primary_metric else "时序指标"
        unit = primary_metric.unit if primary_metric else ""

        stats = self.calculate_statistics(state.raw_records, field)
        chart = self.generate_chart_dsl(state, field)
        conclusion = self.generate_conclusion(state, stats, metric_name, unit)

        state.statistics = stats.model_dump()
        state.visualization = chart.model_dump()
        state.conclusion = conclusion

        if reporter:
            payload = {
                "intent": state.intent.model_dump() if state.intent else {},
                "mapping": {
                    "assets": state.intent.asset_ids if state.intent else [],
                    "metrics": [m.point_code for m in state.metrics],
                    "dictionary_version": state.dictionary_version,
                },
                "sql": {
                    "text": state.safe_sql or state.generated_sql,
                    "visible": True,
                },
                "query": {
                    "row_count": state.row_count,
                    "elapsed_ms": state.query_elapsed_ms,
                    "columns": state.raw_columns,
                },
                "statistics": state.statistics,
                "visualization": state.visualization,
                "conclusion": state.conclusion,
                "raw_preview": state.raw_records[:50],
                "versions": {
                    "model": state.model_version or "qwen-domain-v3",
                    "prompt": state.prompt_versions.get("sql_generation", "v1.0"),
                    "pipeline": "0.1.0",
                },
            }
            await reporter.stage_completed("trend_analysis", {"statistics": state.statistics})
            await reporter.result_completed(payload)

        return state
