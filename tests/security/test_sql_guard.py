import pytest
from src.smart_data.pipeline.components.sql_guard import SQLGuard


def test_sql_guard_allows_valid_select():
    guard = SQLGuard(max_rows=1000)
    raw_sql = "SELECT time, temperature FROM gt_exhaust WHERE time >= '2026-09-01T00:00:00Z' AND time < '2026-09-02T00:00:00Z'"
    is_safe, safe_sql, reasons = guard.validate_and_rewrite(raw_sql)
    assert is_safe is True
    assert "LIMIT 1000" in safe_sql
    assert "temperature" in safe_sql


def test_sql_guard_rejects_drop_and_delete():
    guard = SQLGuard()
    dangerous_sqls = [
        "DROP TABLE gt_exhaust",
        "DELETE FROM gt_exhaust WHERE time > '2026-01-01'",
        "INSERT INTO gt_exhaust (time, val) VALUES ('2026-01-01', 100)",
        "UPDATE gt_exhaust SET temperature = 0",
        "ALTER TABLE gt_exhaust ADD COLUMN hack TEXT",
    ]
    for sql in dangerous_sqls:
        is_safe, _, reasons = guard.validate_and_rewrite(sql)
        assert is_safe is False, f"Failed to reject: {sql}"
        assert any("高危或非只读" in r or "非法根语句" in r for r in reasons)


def test_sql_guard_rejects_multi_statements():
    guard = SQLGuard()
    sql = "SELECT time, temperature FROM gt_exhaust; DROP TABLE users;"
    is_safe, _, reasons = guard.validate_and_rewrite(sql)
    assert is_safe is False
    assert any("多语句" in r for r in reasons)


def test_sql_guard_rejects_star_column():
    guard = SQLGuard()
    sql = "SELECT * FROM gt_exhaust WHERE time >= '2026-09-01T00:00:00Z' AND time < '2026-09-02T00:00:00Z'"
    is_safe, _, reasons = guard.validate_and_rewrite(sql)
    assert is_safe is False
    assert any("SELECT *" in r for r in reasons)


def test_sql_guard_auto_caps_large_limit():
    guard = SQLGuard(max_rows=500)
    sql = "SELECT time, temperature FROM gt_exhaust WHERE time >= '2026-09-01T00:00:00Z' LIMIT 99999"
    is_safe, safe_sql, reasons = guard.validate_and_rewrite(sql)
    assert is_safe is True
    assert "LIMIT 500" in safe_sql


def test_sql_guard_auto_injects_time_window():
    guard = SQLGuard(max_rows=100)
    sql = "SELECT time, temperature FROM gt_exhaust"
    is_safe, safe_sql, reasons = guard.validate_and_rewrite(
        sql,
        start_time_iso="2026-09-01T00:00:00Z",
        end_time_iso="2026-09-02T00:00:00Z",
    )
    assert is_safe is True
    assert "time >= '2026-09-01T00:00:00Z'" in safe_sql
    assert "LIMIT 100" in safe_sql
