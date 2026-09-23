from src.smart_data.pipeline.components.sql_guard import SQLGuard


def test_sql_guard_allows_valid_select():
    guard = SQLGuard(max_rows=1000)
    raw_sql = (
        "SELECT time, temperature FROM gt_exhaust "
        "WHERE time >= '2026-09-01T00:00:00Z' AND time < '2026-09-02T00:00:00Z'"
    )
    is_safe, safe_sql, _reasons = guard.validate_and_rewrite(raw_sql)
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
        "TRUNCATE TABLE gt_exhaust",
        "COPY gt_exhaust TO '/tmp/x'",
        "MERGE INTO gt_exhaust USING src ON TRUE WHEN MATCHED THEN UPDATE SET temperature = 0",
    ]
    for sql in dangerous_sqls:
        is_safe, _, reasons = guard.validate_and_rewrite(sql)
        assert is_safe is False, f"Failed to reject: {sql}"
        assert any("高危或非只读" in item or "非法根语句" in item for item in reasons)


def test_sql_guard_rejects_multi_statements():
    guard = SQLGuard()
    sql = "SELECT time, temperature FROM gt_exhaust; DROP TABLE users;"
    is_safe, _, reasons = guard.validate_and_rewrite(sql)
    assert is_safe is False
    assert any("多语句" in item for item in reasons)


def test_sql_guard_rejects_star_column():
    guard = SQLGuard()
    sql = (
        "SELECT * FROM gt_exhaust "
        "WHERE time >= '2026-09-01T00:00:00Z' AND time < '2026-09-02T00:00:00Z'"
    )
    is_safe, _, reasons = guard.validate_and_rewrite(sql)
    assert is_safe is False
    assert any("SELECT *" in item for item in reasons)


def test_sql_guard_rejects_star_in_cte():
    guard = SQLGuard()
    sql = (
        "WITH t AS (SELECT * FROM gt_exhaust) "
        "SELECT time FROM t "
        "WHERE time >= '2026-09-01T00:00:00Z' AND time < '2026-09-02T00:00:00Z'"
    )
    is_safe, _, _reasons = guard.validate_and_rewrite(sql)
    assert is_safe is False


def test_sql_guard_rejects_comments():
    guard = SQLGuard()
    sql = (
        "SELECT time, temperature FROM gt_exhaust "
        "WHERE time >= '2026-09-01T00:00:00Z' AND time < '2026-09-02T00:00:00Z' -- DROP TABLE x"
    )
    is_safe, _, reasons = guard.validate_and_rewrite(sql)
    assert is_safe is False
    assert any("注释" in item for item in reasons)


def test_sql_guard_rejects_read_csv():
    guard = SQLGuard()
    sql = (
        "SELECT read_csv('/etc/passwd') FROM gt_exhaust "
        "WHERE time >= '2026-09-01T00:00:00Z' AND time < '2026-09-02T00:00:00Z'"
    )
    is_safe, _, reasons = guard.validate_and_rewrite(sql)
    assert is_safe is False
    assert any("危险函数" in item or "未知函数" in item for item in reasons)


def test_sql_guard_rejects_cte_write():
    guard = SQLGuard()
    sql = "WITH t AS (DELETE FROM gt_exhaust) SELECT 1 FROM t"
    is_safe, _, reasons = guard.validate_and_rewrite(sql)
    assert is_safe is False
    assert any("高危或非只读" in item for item in reasons)


def test_sql_guard_rejects_unauthorized_table():
    guard = SQLGuard(max_rows=100)
    sql = (
        "SELECT time, temperature FROM secrets "
        "WHERE time >= '2026-09-01T00:00:00Z' AND time < '2026-09-02T00:00:00Z'"
    )
    is_safe, _, reasons = guard.validate_and_rewrite(
        sql,
        allowed_tables={"gt_exhaust"},
        allowed_columns={"temperature"},
        enforce_whitelist=True,
    )
    assert is_safe is False
    assert any("测量表" in item for item in reasons)


def test_sql_guard_rejects_unauthorized_asset():
    guard = SQLGuard(max_rows=100)
    sql = (
        "SELECT time, temperature FROM gt_exhaust "
        "WHERE time >= '2026-09-01T00:00:00Z' AND time < '2026-09-02T00:00:00Z' "
        "AND asset_id = 'GT-999'"
    )
    is_safe, _, reasons = guard.validate_and_rewrite(
        sql,
        allowed_assets=["GT-001", "GT-002"],
    )
    assert is_safe is False
    assert any("未授权机组" in item for item in reasons)


def test_sql_guard_auto_caps_large_limit():
    guard = SQLGuard(max_rows=500)
    sql = (
        "SELECT time, temperature FROM gt_exhaust "
        "WHERE time >= '2026-09-01T00:00:00Z' AND time < '2026-09-02T00:00:00Z' "
        "LIMIT 99999"
    )
    is_safe, safe_sql, _reasons = guard.validate_and_rewrite(sql)
    assert is_safe is True
    assert "LIMIT 500" in safe_sql


def test_sql_guard_rejects_incomplete_time_filter():
    guard = SQLGuard(max_rows=500)
    sql = "SELECT time, temperature FROM gt_exhaust WHERE time >= '2026-09-01T00:00:00Z' LIMIT 10"
    is_safe, _, reasons = guard.validate_and_rewrite(sql)
    assert is_safe is False
    assert any("时间谓词" in item for item in reasons)


def test_sql_guard_auto_injects_time_window():
    guard = SQLGuard(max_rows=100)
    sql = "SELECT time, temperature FROM gt_exhaust"
    is_safe, safe_sql, _reasons = guard.validate_and_rewrite(
        sql,
        start_time_iso="2026-09-01T00:00:00Z",
        end_time_iso="2026-09-02T00:00:00Z",
    )
    assert is_safe is True
    assert "time >= '2026-09-01T00:00:00Z'" in safe_sql
    assert "LIMIT 100" in safe_sql


def test_sql_guard_injects_asset_scope():
    guard = SQLGuard(max_rows=100)
    sql = (
        "SELECT time, temperature FROM gt_exhaust "
        "WHERE time >= '2026-09-01T00:00:00Z' AND time < '2026-09-02T00:00:00Z'"
    )
    is_safe, safe_sql, _reasons = guard.validate_and_rewrite(sql, allowed_assets=["GT-001"])
    assert is_safe is True
    assert "GT-001" in safe_sql
