from __future__ import annotations

import re
import uuid
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from src.smart_data.domain.intent import QueryIntent, TimeRange
from src.smart_data.domain.query import QueryState
from src.smart_data.infrastructure.database.glossary_repo import GlossaryRepository, default_glossary
from src.smart_data.infrastructure.session_store import ClarificationContext, clarification_store
from src.smart_data.pipeline.reporter import StageReporter

ASSET_TOKEN_RE = re.compile(r"(gt[-_]?\d+|#\d+|\d+号机(?:组)?|\d+号)", re.IGNORECASE)
RELATIVE_SPAN_RE = re.compile(r"(?:最近|过去|近)\s*(\d+)\s*(分钟|小时|天|周)")
AGG_KEYWORDS = {
    "平均": "avg",
    "均值": "avg",
    "最高": "max",
    "最大": "max",
    "峰值": "max",
    "最低": "min",
    "最小": "min",
    "总和": "sum",
    "累计": "sum",
    "次数": "count",
}


class IntentParser:
    """阶段一：意图解析。开发阶段使用确定性规则，不调用大模型。"""

    def __init__(
        self,
        default_timezone: str = "Asia/Shanghai",
        glossary: GlossaryRepository | None = None,
    ):
        self.default_timezone = default_timezone
        self.glossary = glossary or default_glossary
        self.metric_keywords = self.glossary.all_search_terms()

    def _zone(self, tz_name: str):
        try:
            return ZoneInfo(tz_name)
        except (ZoneInfoNotFoundError, Exception):
            if "shanghai" in tz_name.lower() or "beijing" in tz_name.lower():
                return timezone(timedelta(hours=8))
            return timezone.utc

    def parse_time_range(
        self,
        text: str,
        ref_time: datetime | None = None,
        tz_name: str | None = None,
    ) -> tuple[TimeRange | None, bool]:
        zone_name = tz_name or self.default_timezone
        tz = self._zone(zone_name)
        now_local = (ref_time or datetime.now(timezone.utc)).astimezone(tz)
        today_start = now_local.replace(hour=0, minute=0, second=0, microsecond=0)

        def make_range(start: datetime, end: datetime) -> TimeRange:
            return TimeRange(
                start=start.astimezone(timezone.utc),
                end=end.astimezone(timezone.utc),
                timezone=zone_name,
            )

        span = RELATIVE_SPAN_RE.search(text)
        if span:
            amount = int(span.group(1))
            unit = span.group(2)
            delta = {
                "分钟": timedelta(minutes=amount),
                "小时": timedelta(hours=amount),
                "天": timedelta(days=amount),
                "周": timedelta(weeks=amount),
            }[unit]
            return make_range(now_local - delta, now_local), False

        if "今天" in text or "今日" in text:
            return make_range(today_start, today_start + timedelta(days=1)), False
        if "昨天" in text or "昨日" in text:
            return make_range(today_start - timedelta(days=1), today_start), False
        if "上周" in text:
            this_monday = today_start - timedelta(days=today_start.weekday())
            return make_range(this_monday - timedelta(days=7), this_monday), False
        if "本周" in text:
            this_monday = today_start - timedelta(days=today_start.weekday())
            return make_range(this_monday, this_monday + timedelta(days=7)), False
        if "上月" in text:
            first_this_month = today_start.replace(day=1)
            last_month_last_day = first_this_month - timedelta(days=1)
            last_month_start = last_month_last_day.replace(day=1)
            return make_range(last_month_start, first_this_month), False
        if "本月" in text:
            first_this_month = today_start.replace(day=1)
            if first_this_month.month == 12:
                next_month = first_this_month.replace(year=first_this_month.year + 1, month=1)
            else:
                next_month = first_this_month.replace(month=first_this_month.month + 1)
            return make_range(first_this_month, next_month), False
        if "过去 1 小时" in text or "过去1小时" in text or "近1小时" in text:
            return make_range(now_local - timedelta(hours=1), now_local), False
        if "过去 7 天" in text or "过去7天" in text or "近7天" in text:
            return make_range(now_local - timedelta(days=7), now_local), False
        if "过去 24 小时" in text or "过去24小时" in text:
            return make_range(now_local - timedelta(hours=24), now_local), False

        if any(word in text for word in ["最近", "前段时间", "近期", "以往"]):
            return None, True
        return None, False

    def extract_assets(self, text: str) -> list[str]:
        normalized: list[str] = []
        for token in ASSET_TOKEN_RE.findall(text):
            asset_id = _normalize_asset(token)
            if asset_id and asset_id not in normalized:
                normalized.append(asset_id)
        return normalized

    def extract_metrics(self, text: str) -> list[str]:
        lowered = text.lower()
        matches: list[tuple[int, int, str]] = []
        for keyword in self.metric_keywords:
            needle = keyword.lower()
            start = 0
            while True:
                index = lowered.find(needle, start)
                if index < 0:
                    break
                end = index + len(needle)
                overlapped = any(not (end <= left or index >= right) for left, right, _ in matches)
                if not overlapped:
                    original = text[index:end] if text[index:end].lower() == needle else keyword
                    matches.append((index, end, original))
                start = index + 1
        matches.sort(key=lambda item: item[0])
        return [item[2] for item in matches]

    def extract_aggregation(self, text: str) -> str | None:
        for keyword, agg in AGG_KEYWORDS.items():
            if keyword in text:
                return agg
        return None

    def extract_group_by(self, text: str) -> list[str]:
        group_by: list[str] = []
        if any(token in text for token in ["按天", "每天", "逐日"]):
            group_by.append("day")
        if any(token in text for token in ["按小时", "每小时"]):
            group_by.append("hour")
        if any(token in text for token in ["按机组", "分机组"]):
            group_by.append("asset")
        return group_by

    async def run(self, state: QueryState, reporter: StageReporter | None = None) -> QueryState:
        if reporter:
            await reporter.stage_started("intent_parsing", {"question": state.question})

        tz_name = state.timezone or self.default_timezone
        time_range, is_time_ambiguous = self.parse_time_range(state.question, tz_name=tz_name)
        assets = self.extract_assets(state.question)
        metrics = self.extract_metrics(state.question)
        aggregation = self.extract_aggregation(state.question)
        group_by = self.extract_group_by(state.question)

        operation: str = "query"
        if any(word in state.question for word in ["相关", "相关性", "联动"]):
            operation = "correlation"
        elif len(assets) > 1 or "对比" in state.question:
            operation = "compare"
        elif any(word in state.question for word in ["最高", "最大", "最低", "最小", "极值", "峰值"]):
            operation = "extreme"
        elif aggregation is not None:
            operation = "aggregate"

        clarification_questions: list[str] = []
        if is_time_ambiguous or time_range is None:
            clarification_questions.append("请明确查询的时间范围（例如：今天、上周、过去 24 小时）。")
        if not assets:
            if len(state.allowed_asset_ids) == 1:
                assets = list(state.allowed_asset_ids)
            else:
                clarification_questions.append("请选择需要查询的机组（例如：#1 机组 GT-001 或 #2 机组 GT-002）。")
        if not metrics and not state.forced_metric_codes:
            clarification_questions.append("请指定需要查询的指标参数（例如：排气温度、转子转速、发电机功率）。")

        intent = QueryIntent(
            operation=operation,  # type: ignore[arg-type]
            asset_ids=assets,
            metric_terms=metrics or list(state.forced_metric_codes),
            time_range=time_range,
            aggregation=aggregation,
            group_by=group_by,
            needs_clarification=bool(clarification_questions),
            clarification_questions=clarification_questions,
        )
        state.intent = intent

        if reporter:
            if intent.needs_clarification:
                questions_payload = [
                    {"id": f"q_{index}", "text": question, "type": "text"}
                    for index, question in enumerate(clarification_questions)
                ]
                clarification_id = state.clarification_id or f"clarify_{uuid.uuid4().hex[:12]}"
                clarification_store.put(
                    ClarificationContext(
                        clarification_id=clarification_id,
                        user_id=state.user_id,
                        question=state.question,
                        timezone=state.timezone,
                        locale=state.locale,
                        allowed_asset_ids=state.allowed_asset_ids,
                        stage="intent_parsing",
                        questions=questions_payload,
                        expires_at=clarification_store.expires_at(),
                    )
                )
                await reporter.clarification_required(
                    stage="intent_parsing",
                    questions=questions_payload,
                    clarification_id=clarification_id,
                )
            else:
                await reporter.stage_completed(
                    "intent_parsing",
                    {
                        "operation": intent.operation,
                        "assets": intent.asset_ids,
                        "metrics": intent.metric_terms,
                        "group_by": intent.group_by,
                        "time_range": {
                            "start": intent.time_range.start.isoformat(),
                            "end": intent.time_range.end.isoformat(),
                        }
                        if intent.time_range
                        else None,
                    },
                )
        return state


def _normalize_asset(token: str) -> str | None:
    cleaned = token.strip().upper().replace("机组", "").replace("号机", "号")
    match = re.search(r"GT[-_]?(\d+)", cleaned)
    if match:
        return f"GT-{int(match.group(1)):03d}"
    match = re.search(r"#(\d+)", cleaned)
    if match:
        return f"GT-{int(match.group(1)):03d}"
    match = re.search(r"^(\d+)号", cleaned)
    if match:
        return f"GT-{int(match.group(1)):03d}"
    return None
