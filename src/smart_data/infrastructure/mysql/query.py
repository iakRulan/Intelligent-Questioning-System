from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import text

from src.smart_data.config import settings
from src.smart_data.infrastructure.mysql.engine import get_engine


def execute_readonly_sql(sql: str, max_rows: int | None = None) -> tuple[list[str], list[dict[str, Any]]]:
    limit = max_rows or settings.query_policy.max_result_rows
    with get_engine().connect() as conn:
        result = conn.execute(text(sql))
        columns = list(result.keys())
        records: list[dict[str, Any]] = []
        for row in result:
            if len(records) >= limit:
                break
            mapping = dict(row._mapping)
            records.append({key: _normalize_value(mapping[key]) for key in columns})
        return columns, records


def _normalize_value(value: Any) -> Any:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.strftime("%Y-%m-%dT%H:%M:%SZ")
        return value.isoformat().replace("+00:00", "Z")
    if hasattr(value, "as_py"):
        return _normalize_value(value.as_py())
    return value
