from src.smart_data.domain.query import QueryState
from src.smart_data.infrastructure.database.glossary_repo import default_glossary, GlossaryRepository
from src.smart_data.pipeline.reporter import StageReporter


class SemanticMapper:
    """阶段二：语义与指标字典映射组件。

    将业务语言（口语化别名）通过版本化字典精确对齐为标准测点编码、InfluxDB 表与字段，并执行权限域核查。
    """

    def __init__(self, glossary: GlossaryRepository | None = None):
        self.glossary = glossary or default_glossary

    async def run(self, state: QueryState, reporter: StageReporter | None = None) -> QueryState:
        if reporter:
            await reporter.stage_started("semantic_mapping", {"dictionary_version": self.glossary.version})

        if not state.intent or state.intent.needs_clarification:
            return state

        # 1. 机组权限域二次校验
        for asset_id in state.intent.asset_ids:
            if state.allowed_asset_ids and asset_id not in state.allowed_asset_ids:
                error_msg = f"无权限访问机组资产: {asset_id}"
                state.error_code = "SDQ-403-SCOPE"
                state.error_message = error_msg
                if reporter:
                    await reporter.stage_failed("semantic_mapping", state.error_code, error_msg)
                return state

        # 2. 指标词典解析与映射
        mapped_metrics = []
        clarification_questions = []

        for term in state.intent.metric_terms:
            candidates = self.glossary.resolve_metric(term)
            if len(candidates) == 1:
                entry = candidates[0]
                mapped_metrics.append(entry.to_metric_ref(aggregation=state.intent.aggregation))
            elif len(candidates) > 1:
                # 歧义词处理（如"效率"）
                options = [{"value": c.point_code, "label": c.business_name} for c in candidates]
                clarification_questions.append({
                    "id": f"choice_{term}",
                    "text": f"检测到您提及的「{term}」对应多个专业指标，请明确选择：",
                    "type": "single_choice",
                    "options": options,
                })
            else:
                clarification_questions.append({
                    "id": f"unknown_{term}",
                    "text": f"系统字典暂未收录指标「{term}」，请尝试使用标准名称（如平均排气温度、转子转速）。",
                    "type": "text",
                })

        state.dictionary_version = self.glossary.version

        if clarification_questions:
            state.intent.needs_clarification = True
            if reporter:
                await reporter.clarification_required(
                    stage="semantic_mapping",
                    questions=clarification_questions,
                    clarification_id=f"clarify_{state.query_id}",
                )
            return state

        state.metrics = mapped_metrics

        if reporter:
            summary = {
                "dictionary_version": state.dictionary_version,
                "mapped_metrics": [
                    {
                        "business_name": m.business_name,
                        "point_code": m.point_code,
                        "table": m.measurement,
                        "field": m.field,
                        "unit": m.unit,
                    }
                    for m in state.metrics
                ],
            }
            await reporter.stage_completed("semantic_mapping", summary)

        return state
