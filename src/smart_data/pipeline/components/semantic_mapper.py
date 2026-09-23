import uuid

from src.smart_data.config import settings
from src.smart_data.domain.query import QueryState
from src.smart_data.infrastructure.database.glossary_repo import GlossaryRepository, default_glossary
from src.smart_data.infrastructure.session_store import ClarificationContext, clarification_store
from src.smart_data.pipeline.reporter import StageReporter


class SemanticMapper:
    """阶段二：指标字典映射与权限域核验。"""

    def __init__(self, glossary: GlossaryRepository | None = None):
        self.glossary = glossary or default_glossary

    async def run(self, state: QueryState, reporter: StageReporter | None = None) -> QueryState:
        if reporter:
            await reporter.stage_started("semantic_mapping", {"dictionary_version": self.glossary.version})

        if not state.intent or state.intent.needs_clarification:
            return state

        for asset_id in state.intent.asset_ids:
            if state.allowed_asset_ids and asset_id not in state.allowed_asset_ids:
                state.error_code = "SDQ-403-SCOPE"
                state.error_message = f"无权限访问机组资产: {asset_id}"
                if reporter:
                    await reporter.stage_failed("semantic_mapping", state.error_code, state.error_message)
                return state

        policy = settings.query_policy
        if len(state.intent.asset_ids) > policy.max_assets_per_query:
            state.error_code = "SDQ-422-INTENT"
            state.error_message = f"单次查询机组数超过上限 {policy.max_assets_per_query}"
            if reporter:
                await reporter.stage_failed("semantic_mapping", state.error_code, state.error_message)
            return state

        terms = list(state.forced_metric_codes) if state.forced_metric_codes else list(state.intent.metric_terms)
        mapped_metrics = []
        clarification_questions: list[dict] = []
        seen_codes: set[str] = set()

        for term in terms:
            candidates = self.glossary.resolve_metric(term)
            if len(candidates) == 1:
                entry = candidates[0]
                if entry.point_code in seen_codes:
                    continue
                seen_codes.add(entry.point_code)
                mapped_metrics.append(entry.to_metric_ref(aggregation=state.intent.aggregation))
            elif len(candidates) > 1:
                options = [{"value": item.point_code, "label": item.business_name} for item in candidates]
                clarification_questions.append(
                    {
                        "id": f"choice_{term}",
                        "text": f"检测到您提及的「{term}」对应多个专业指标，请明确选择：",
                        "type": "single_choice",
                        "options": options,
                    }
                )
            else:
                clarification_questions.append(
                    {
                        "id": f"unknown_{term}",
                        "text": f"系统字典暂未收录指标「{term}」，请尝试使用标准名称（如平均排气温度、转子转速）。",
                        "type": "text",
                    }
                )

        state.dictionary_version = self.glossary.version

        if clarification_questions:
            state.intent.needs_clarification = True
            if reporter:
                clarification_id = state.clarification_id or f"clarify_{uuid.uuid4().hex[:12]}"
                clarification_store.put(
                    ClarificationContext(
                        clarification_id=clarification_id,
                        user_id=state.user_id,
                        question=state.question,
                        timezone=state.timezone,
                        locale=state.locale,
                        allowed_asset_ids=state.allowed_asset_ids,
                        stage="semantic_mapping",
                        questions=clarification_questions,
                        expires_at=clarification_store.expires_at(),
                    )
                )
                await reporter.clarification_required(
                    stage="semantic_mapping",
                    questions=clarification_questions,
                    clarification_id=clarification_id,
                )
            return state

        if len(mapped_metrics) > policy.max_metrics_per_query:
            state.error_code = "SDQ-422-MAPPING"
            state.error_message = f"单次查询指标数超过上限 {policy.max_metrics_per_query}"
            if reporter:
                await reporter.stage_failed("semantic_mapping", state.error_code, state.error_message)
            return state

        if not mapped_metrics:
            state.error_code = "SDQ-422-MAPPING"
            state.error_message = "指标无法映射到有效测点"
            if reporter:
                await reporter.stage_failed("semantic_mapping", state.error_code, state.error_message)
            return state

        state.metrics = mapped_metrics
        if reporter:
            await reporter.stage_completed(
                "semantic_mapping",
                {
                    "dictionary_version": state.dictionary_version,
                    "mapped_metrics": [
                        {
                            "business_name": metric.business_name,
                            "point_code": metric.point_code,
                            "table": metric.measurement,
                            "field": metric.field,
                            "unit": metric.unit,
                        }
                        for metric in state.metrics
                    ],
                },
            )
        return state
