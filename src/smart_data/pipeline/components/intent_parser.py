import re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from typing import Any
from src.smart_data.domain.intent import QueryIntent, TimeRange
from src.smart_data.domain.query import QueryState
from src.smart_data.pipeline.reporter import StageReporter


class IntentParser:
    """阶段一：意图解析组件。

    从用户自然语言问句中抽取操作类型、资产对象、业务指标、时间窗口及聚合方式。
    若发现信息不完整或模糊，标记 needs_clarification 并生成澄清提问。
    """

    ASSET_PATTERN = re.compile(r"(gt[-_]?\d+|#\d+|\d+号机(?:组)?|\d+号)", re.IGNORECASE)
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
    METRIC_KEYWORDS = ["排温", "排气温度", "转速", "功率", "发电功率", "燃油流量", "燃耗", "振动", "效率"]

    def __init__(self, default_timezone: str = "Asia/Shanghai"):
        self.default_timezone = default_timezone

    def parse_time_range(self, text: str, ref_time: datetime | None = None) -> tuple[TimeRange | None, bool]:
        """确定性解析时间窗口。

        Returns:
            (time_range, is_ambiguous)
        """
        try:
            tz = ZoneInfo(self.default_timezone)
        except Exception:
            # 针对 Windows 无系统 tzdata 时的跨平台健壮降级 (UTC+8)
            if "shanghai" in self.default_timezone.lower() or "beijing" in self.default_timezone.lower():
                tz = timezone(timedelta(hours=8))
            else:
                tz = timezone.utc

        now_local = (ref_time or datetime.now(timezone.utc)).astimezone(tz)
        today_start = now_local.replace(hour=0, minute=0, second=0, microsecond=0)

        # 1. 拦截模糊表达
        if any(w in text for w in ["最近", "前段时间", "近期", "以往"]):
            if not any(w in text for w in ["过去", "小时", "天", "周", "月"]):
                return None, True

        # 2. 相对自然周期
        if "今天" in text or "今日" in text:
            start = today_start
            end = today_start + timedelta(days=1)
            return TimeRange(start=start.astimezone(timezone.utc), end=end.astimezone(timezone.utc)), False

        if "昨天" in text or "昨日" in text:
            start = today_start - timedelta(days=1)
            end = today_start
            return TimeRange(start=start.astimezone(timezone.utc), end=end.astimezone(timezone.utc)), False

        if "上周" in text:
            # 找到上周一 00:00 到本周一 00:00
            this_monday = today_start - timedelta(days=today_start.weekday())
            last_monday = this_monday - timedelta(days=7)
            return TimeRange(start=last_monday.astimezone(timezone.utc), end=this_monday.astimezone(timezone.utc)), False

        if "本周" in text:
            this_monday = today_start - timedelta(days=today_start.weekday())
            next_monday = this_monday + timedelta(days=7)
            return TimeRange(start=this_monday.astimezone(timezone.utc), end=next_monday.astimezone(timezone.utc)), False

        if "过去 1 小时" in text or "过去1小时" in text or "近1小时" in text:
            start = now_local - timedelta(hours=1)
            return TimeRange(start=start.astimezone(timezone.utc), end=now_local.astimezone(timezone.utc)), False

        if "过去 7 天" in text or "过去7天" in text or "近7天" in text:
            start = now_local - timedelta(days=7)
            return TimeRange(start=start.astimezone(timezone.utc), end=now_local.astimezone(timezone.utc)), False

        if "过去 24 小时" in text or "过去24小时" in text:
            start = now_local - timedelta(hours=24)
            return TimeRange(start=start.astimezone(timezone.utc), end=now_local.astimezone(timezone.utc)), False

        return None, False

    def extract_assets(self, text: str) -> list[str]:
        raw_assets = self.ASSET_PATTERN.findall(text)
        normalized = []
        for a in raw_assets:
            a_clean = a.upper()
            if "1号" in a_clean or "#1" in a_clean or "GT-001" in a_clean or "GT1" in a_clean:
                normalized.append("GT-001")
            elif "2号" in a_clean or "#2" in a_clean or "GT-002" in a_clean or "GT2" in a_clean:
                normalized.append("GT-002")
            else:
                normalized.append(a_clean)
        return list(dict.fromkeys(normalized))

    def extract_metrics(self, text: str) -> list[str]:
        found = []
        for kw in self.METRIC_KEYWORDS:
            if kw in text:
                found.append(kw)
        return found

    def extract_aggregation(self, text: str) -> str | None:
        for kw, agg in self.AGG_KEYWORDS.items():
            if kw in text:
                return agg
        return None

    async def run(self, state: QueryState, reporter: StageReporter | None = None) -> QueryState:
        if reporter:
            await reporter.stage_started("intent_parsing", {"question": state.question})

        text = state.question
        time_range, is_time_ambiguous = self.parse_time_range(text)
        assets = self.extract_assets(text)
        metrics = self.extract_metrics(text)
        agg = self.extract_aggregation(text)

        # 确定操作类型
        op = "query"
        if len(assets) > 1:
            op = "compare"
        elif any(w in text for w in ["最高", "最大", "最低", "最小", "极值"]):
            op = "extreme"
        elif agg is not None:
            op = "aggregate"

        # 判断是否需要澄清
        clarification_questions = []
        if is_time_ambiguous or time_range is None:
            clarification_questions.append("请明确查询的时间范围（例如：今天、上周、过去 24 小时）。")

        if not assets:
            # 若用户仅有单台机组权限，可默认绑定；否则澄清
            if len(state.allowed_asset_ids) == 1:
                assets = state.allowed_asset_ids
            else:
                clarification_questions.append("请选择需要查询的机组（例如：#1 机组 GT-001 或 #2 机组 GT-002）。")

        if not metrics:
            clarification_questions.append("请指定需要查询的指标参数（例如：排气温度、转子转速、发电机功率）。")

        intent = QueryIntent(
            operation=op,
            asset_ids=assets,
            metric_terms=metrics,
            time_range=time_range,
            aggregation=agg,
            needs_clarification=bool(clarification_questions),
            clarification_questions=clarification_questions,
        )

        state.intent = intent

        if reporter:
            if intent.needs_clarification:
                # 触发澄清事件
                questions_payload = [
                    {"id": f"q_{i}", "text": q, "type": "text"}
                    for i, q in enumerate(clarification_questions)
                ]
                await reporter.clarification_required(
                    stage="intent_parsing",
                    questions=questions_payload,
                    clarification_id=f"clarify_{state.query_id}",
                )
            else:
                await reporter.stage_completed(
                    "intent_parsing",
                    {
                        "operation": intent.operation,
                        "assets": intent.asset_ids,
                        "metrics": intent.metric_terms,
                        "time_range": {
                            "start": intent.time_range.start.isoformat(),
                            "end": intent.time_range.end.isoformat(),
                        } if intent.time_range else None,
                    },
                )

        return state
