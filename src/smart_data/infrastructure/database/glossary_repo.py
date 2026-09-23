from __future__ import annotations

import json

from sqlalchemy import text

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
    """业务指标字典仓储。可从 MySQL 加载，失败时使用内置默认字典。"""

    def __init__(self, version: str = "dict-v20260901"):
        self.version = version
        self.source = "memory"
        self._entries: dict[str, MetricDictionaryEntry] = {}
        self._alias_map: dict[str, list[str]] = {}
        self._init_default_gt_metrics()

    def _init_default_gt_metrics(self) -> None:
        default_metrics = [
            MetricDictionaryEntry(
                point_code="T48_AVG",
                business_name="平均排气温度",
                measurement="gt_telemetry",
                field="temperature",
                unit="℃",
                aliases=["排温", "排气温度", "透平排温", "t48", "t4_avg"],
            ),
            MetricDictionaryEntry(
                point_code="N2_SPEED",
                business_name="燃机转子转速",
                measurement="gt_telemetry",
                field="speed",
                unit="rpm",
                aliases=["转速", "燃机转速", "n2", "转子转速"],
            ),
            MetricDictionaryEntry(
                point_code="GEN_POWER",
                business_name="发电机有功功率",
                measurement="gt_telemetry",
                field="power",
                unit="MW",
                aliases=["发电功率", "功率", "有功功率", "输出功率", "负荷"],
            ),
            MetricDictionaryEntry(
                point_code="FUEL_FLOW",
                business_name="燃油质量流量",
                measurement="gt_telemetry",
                field="flow_rate",
                unit="kg/s",
                aliases=["燃油流量", "燃耗", "耗气量", "燃料流量"],
            ),
            MetricDictionaryEntry(
                point_code="VIB_MAX",
                business_name="轴承振动有效值",
                measurement="gt_telemetry",
                field="vibration",
                unit="μm",
                aliases=["振动", "轴振", "瓦振", "最大振动值"],
            ),
            MetricDictionaryEntry(
                point_code="GEN_EFF",
                business_name="发电效率",
                measurement="gt_telemetry",
                field="generator_efficiency",
                unit="%",
                aliases=["发电效率", "效率"],
            ),
            MetricDictionaryEntry(
                point_code="THERMAL_EFF",
                business_name="热效率",
                measurement="gt_telemetry",
                field="thermal_efficiency",
                unit="%",
                aliases=["热效率", "循环热效率", "效率"],
            ),
        ]
        self._entries.clear()
        self._alias_map.clear()
        for entry in default_metrics:
            self._register(entry)

    def _register(self, entry: MetricDictionaryEntry) -> None:
        self._entries[entry.point_code] = entry
        self._add_alias(entry.business_name.lower(), entry.point_code)
        self._add_alias(entry.point_code.lower(), entry.point_code)
        for alias in entry.aliases:
            self._add_alias(alias.lower(), entry.point_code)

    def _add_alias(self, alias: str, point_code: str) -> None:
        bucket = self._alias_map.setdefault(alias, [])
        if point_code not in bucket:
            bucket.append(point_code)

    def all_search_terms(self) -> list[str]:
        return sorted(self._alias_map.keys(), key=len, reverse=True)

    def get_by_code(self, point_code: str) -> MetricDictionaryEntry | None:
        return self._entries.get(point_code)

    def resolve_metric(self, term: str) -> list[MetricDictionaryEntry]:
        clean_term = term.strip().lower()
        if not clean_term:
            return []
        codes = self._alias_map.get(clean_term, [])
        return [self._entries[code] for code in codes if code in self._entries]

    def reload_from_mysql(self, engine) -> None:
        with engine.connect() as conn:
            version = conn.execute(
                text(
                    """
                    SELECT version_code FROM metric_dictionary_version
                    WHERE status='active'
                    ORDER BY effective_at DESC
                    LIMIT 1
                    """
                )
            ).scalar()
            if not version:
                return
            rows = conn.execute(
                text(
                    """
                    SELECT e.point_code, e.business_name, e.measurement, e.field_name, e.unit,
                           e.allowed_aggregations, a.alias
                    FROM metric_dictionary_entry e
                    JOIN metric_dictionary_version v ON v.id = e.version_id
                    LEFT JOIN metric_alias a ON a.entry_id = e.id
                    WHERE v.version_code = :ver AND e.enabled = 1
                    """
                ),
                {"ver": version},
            ).mappings()
            grouped: dict[str, MetricDictionaryEntry] = {}
            for row in rows:
                code = row["point_code"]
                if code not in grouped:
                    try:
                        aggs = json.loads(row["allowed_aggregations"] or "[]")
                    except Exception:
                        aggs = ["avg", "max", "min", "count", "sum"]
                    grouped[code] = MetricDictionaryEntry(
                        point_code=code,
                        business_name=row["business_name"],
                        measurement=row["measurement"],
                        field=row["field_name"],
                        unit=row["unit"] or "",
                        aliases=[],
                        allowed_aggregations=aggs,
                    )
                alias = row["alias"]
                if alias and alias not in grouped[code].aliases:
                    grouped[code].aliases.append(alias)
        if not grouped:
            return
        self.version = version
        self.source = "mysql"
        self._entries = grouped
        self._alias_map = {}
        for entry in grouped.values():
            self._register(entry)


default_glossary = GlossaryRepository()
