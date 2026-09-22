from typing import Any
from src.smart_data.domain.query import MetricRef


class MetricDictionaryEntry:
    def __init__(
        self,
        point_code: str,
        business_name: str,
        measurement: str,
        field: str,
        unit: str,
        aliases: list[str],
        asset_types: list[str] | None = None,
        allowed_aggregations: list[str] | None = None,
    ):
        self.point_code = point_code
        self.business_name = business_name
        self.measurement = measurement
        self.field = field
        self.unit = unit
        self.aliases = aliases
        self.asset_types = asset_types or ["LM2500", "GT25000", "GENERIC"]
        self.allowed_aggregations = allowed_aggregations or ["avg", "max", "min", "count", "sum"]

    def to_metric_ref(self, aggregation: str | None = None) -> MetricRef:
        return MetricRef(
            business_name=self.business_name,
            point_code=self.point_code,
            measurement=self.measurement,
            field=self.field,
            unit=self.unit,
            aggregation=aggregation,
        )


class GlossaryRepository:
    """业务指标字典仓储，管理标准测点定义、口语别名及歧义判别。"""

    def __init__(self, version: str = "dict-v20260901"):
        self.version = version
        self._entries: dict[str, MetricDictionaryEntry] = {}
        self._alias_map: dict[str, list[str]] = {}
        self._init_default_gt_metrics()

    def _init_default_gt_metrics(self) -> None:
        default_metrics = [
            MetricDictionaryEntry(
                point_code="T48_AVG",
                business_name="平均排气温度",
                measurement="gt_exhaust",
                field="temperature",
                unit="℃",
                aliases=["排温", "排气温度", "透平排温", "t48", "t4_avg"],
            ),
            MetricDictionaryEntry(
                point_code="N2_SPEED",
                business_name="燃机转子转速",
                measurement="gt_speed",
                field="speed",
                unit="rpm",
                aliases=["转速", "燃机转速", "n2", "转子转速"],
            ),
            MetricDictionaryEntry(
                point_code="GEN_POWER",
                business_name="发电机有功功率",
                measurement="gt_generator",
                field="power",
                unit="MW",
                aliases=["发电功率", "功率", "有功功率", "输出功率", "负荷"],
            ),
            MetricDictionaryEntry(
                point_code="FUEL_FLOW",
                business_name="燃油质量流量",
                measurement="gt_fuel",
                field="flow_rate",
                unit="kg/s",
                aliases=["燃油流量", "燃耗", "耗气量", "燃料流量"],
            ),
            MetricDictionaryEntry(
                point_code="VIB_MAX",
                business_name="轴承振动有效值",
                measurement="gt_vibration",
                field="vibration",
                unit="μm",
                aliases=["振动", "轴振", "瓦振", "最大振动值"],
            ),
            # 歧义词项示例：用户提"效率"时，可匹配以下两种
            MetricDictionaryEntry(
                point_code="GEN_EFF",
                business_name="发电效率",
                measurement="gt_efficiency",
                field="generator_efficiency",
                unit="%",
                aliases=["发电效率", "效率"],
            ),
            MetricDictionaryEntry(
                point_code="THERMAL_EFF",
                business_name="热效率",
                measurement="gt_efficiency",
                field="thermal_efficiency",
                unit="%",
                aliases=["热效率", "循环热效率", "效率"],
            ),
        ]

        for entry in default_metrics:
            self._entries[entry.point_code] = entry
            # 映射标准名
            self._add_alias(entry.business_name.lower(), entry.point_code)
            self._add_alias(entry.point_code.lower(), entry.point_code)
            for a in entry.aliases:
                self._add_alias(a.lower(), entry.point_code)

    def _add_alias(self, alias: str, point_code: str) -> None:
        if alias not in self._alias_map:
            self._alias_map[alias] = []
        if point_code not in self._alias_map[alias]:
            self._alias_map[alias].append(point_code)

    def resolve_metric(self, term: str) -> list[MetricDictionaryEntry]:
        """依据自然语言术语检索匹配的指标条目。

        Returns:
            若返回 1 个元素则为唯一匹配；
            若返回多个元素则代表存在歧义（需澄清）；
            若返回 0 个代表未识别指标。
        """
        clean_term = term.strip().lower()

        # 1. 尝试精确查找别名映射
        if clean_term in self._alias_map:
            matched_codes = self._alias_map[clean_term]
            return [self._entries[code] for code in matched_codes if code in self._entries]

        # 2. 尝试子串模糊匹配
        matches: set[str] = set()
        for alias, codes in self._alias_map.items():
            if clean_term in alias or alias in clean_term:
                matches.update(codes)

        return [self._entries[code] for code in matches if code in self._entries]


# 默认单例
default_glossary = GlossaryRepository()
