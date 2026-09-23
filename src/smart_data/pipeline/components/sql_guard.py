from __future__ import annotations

import re

import sqlglot
from sqlglot import exp

from src.smart_data.config import settings
from src.smart_data.domain.errors import SQLSecurityError

_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_ASSET_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
_STRING_LITERAL_RE = re.compile(r"'(?:''|[^'])*'")

_FORBIDDEN_TYPE_NAMES = (
    "Insert",
    "Update",
    "Delete",
    "Create",
    "Drop",
    "Alter",
    "Command",
    "Copy",
    "Merge",
    "TruncateTable",
    "Pragma",
    "Grant",
    "Revoke",
    "Analyze",
    "Attach",
    "Detach",
    "Execute",
    "Set",
    "Use",
)

FORBIDDEN_EXPRESSIONS = tuple(
    cls for name in _FORBIDDEN_TYPE_NAMES if (cls := getattr(exp, name, None)) is not None
)

ALLOWED_ANONYMOUS_FUNCTIONS = {
    "date_bin",
    "date_trunc",
    "date_add",
    "date_diff",
    "to_timestamp",
    "coalesce",
    "nullif",
    "greatest",
    "least",
    "round",
    "abs",
    "floor",
    "ceil",
    "ceiling",
    "concat",
    "lower",
    "upper",
    "length",
    "avg",
    "max",
    "min",
    "sum",
    "count",
}

DANGEROUS_FUNCTIONS = {
    "read_csv",
    "read_parquet",
    "read_json",
    "load",
    "sleep",
    "version",
    "current_user",
    "system_user",
    "pg_read_file",
    "file",
    "copy",
}


class SQLGuard:
    """基于 sqlglot AST 的只读 SQL 安全网关与改写器。"""

    def __init__(
        self,
        allowed_tables: set[str] | None = None,
        allowed_columns: set[str] | None = None,
        max_rows: int | None = None,
    ):
        self.allowed_tables = {item.lower() for item in (allowed_tables or set())}
        self.allowed_columns = {item.lower() for item in (allowed_columns or set())}
        self.max_rows = max_rows or settings.query_policy.max_result_rows

    def validate_and_rewrite(
        self,
        raw_sql: str,
        start_time_iso: str | None = None,
        end_time_iso: str | None = None,
        allowed_tables: set[str] | None = None,
        allowed_columns: set[str] | None = None,
        allowed_assets: list[str] | None = None,
        enforce_whitelist: bool | None = None,
    ) -> tuple[bool, str, list[str]]:
        reasons: list[str] = []
        tables = {item.lower() for item in (allowed_tables or self.allowed_tables)}
        columns = {item.lower() for item in (allowed_columns or self.allowed_columns)}
        if enforce_whitelist is None:
            enforce_whitelist = bool(tables)

        clean_sql = (raw_sql or "").strip()
        if not clean_sql:
            return False, "", ["空 SQL 语句"]

        stripped = _STRING_LITERAL_RE.sub("''", clean_sql)
        if "--" in stripped or "/*" in stripped or "*/" in stripped:
            return False, "", ["拒绝 SQL 注释中隐藏的附加语句"]

        if ";" in clean_sql.rstrip(";"):
            return False, "", ["拒绝多语句执行 (Multiple SQL statements detected)"]

        clean_sql = clean_sql.rstrip(";").strip()

        try:
            parsed = sqlglot.parse(clean_sql)
        except Exception as exc:
            return False, "", [f"SQL 语法解析失败: {exc}"]

        if not parsed or any(item is None for item in parsed):
            return False, "", ["无效的 SQL 语句结构"]
        if len(parsed) != 1:
            return False, "", ["拒绝多语句执行 (Multiple SQL statements detected)"]

        tree = parsed[0]
        if isinstance(tree, exp.Union):
            return False, "", ["非法根语句类型: Union，仅允许单条只读 SELECT 语句"]
        if not isinstance(tree, exp.Select):
            return False, "", [f"非法根语句类型: {tree.__class__.__name__}，仅允许只读 SELECT 语句"]

        for node in tree.walk():
            if isinstance(node, FORBIDDEN_EXPRESSIONS):
                return False, "", [f"检测到高危或非只读操作: {node.__class__.__name__}"]
            danger = _dangerous_function_name(node)
            if danger:
                return False, "", [f"禁止调用危险函数: {danger}"]
            if isinstance(node, exp.Anonymous):
                func_name = (node.name or "").lower()
                if func_name not in ALLOWED_ANONYMOUS_FUNCTIONS:
                    return False, "", [f"禁止调用未知函数: {func_name}"]

        for select_node in tree.find_all(exp.Select):
            for select_expr in select_node.expressions:
                if isinstance(select_expr, exp.Star):
                    return False, "", ["禁止使用 SELECT * 通配符全量拉取，必须指明具体列名"]
                if isinstance(select_expr, exp.Column) and isinstance(select_expr.this, exp.Star):
                    return False, "", ["禁止使用 SELECT * 通配符全量拉取，必须指明具体列名"]
            if any(isinstance(node, exp.Star) for node in select_node.walk()):
                return False, "", ["禁止使用 SELECT * 通配符全量拉取，必须指明具体列名"]

        cte_names = _cte_aliases(tree)
        if enforce_whitelist and tables:
            for table in tree.find_all(exp.Table):
                table_name = (table.name or "").lower()
                if table_name in cte_names:
                    continue
                if table_name not in tables:
                    reasons.append(f"未授权或不存在的测量表: {table_name}")
            if columns:
                safe_columns = columns | {"time", "asset_id"}
                for col in tree.find_all(exp.Column):
                    if isinstance(col.this, exp.Star):
                        continue
                    col_name = (col.name or "").lower()
                    if col_name and col_name not in safe_columns and col_name not in cte_names:
                        reasons.append(f"未授权或不存在的列名: {col_name}")
            if reasons:
                return False, "", reasons

        select = tree
        has_ge, has_lt = _time_bounds(select)
        has_time_filter = has_ge and has_lt
        if not has_time_filter and start_time_iso and end_time_iso:
            select = select.where(f"time >= '{start_time_iso}' AND time < '{end_time_iso}'")
            reasons.append("原始语句未包含时间范围，已自动注入指定时间窗口约束")
        elif not has_time_filter:
            return False, "", ["时序数据库查询必须包含明确的时间谓词 (time filter)"]

        if allowed_assets:
            safe_assets = _sanitize_assets(allowed_assets)
            if not safe_assets:
                return False, "", ["授权机组列表非法"]
            asset_status = _asset_filter_status(select, safe_assets)
            if asset_status == "unauthorized":
                return False, "", ["SQL 中包含未授权机组过滤条件"]
            if asset_status == "missing":
                in_list = ", ".join(f"'{item}'" for item in safe_assets)
                select = select.where(f"asset_id IN ({in_list})")
                reasons.append("已注入授权机组过滤条件")

        limit_node = select.args.get("limit")
        if limit_node is None:
            select = select.limit(self.max_rows)
        else:
            try:
                current_limit = int(limit_node.expression.this)
            except Exception:
                current_limit = self.max_rows + 1
            if current_limit > self.max_rows:
                select = select.limit(self.max_rows)
                reasons.append(f"LIMIT 值超出策略上限，已自动收敛至 {self.max_rows}")

        return True, select.sql(), reasons


def _dangerous_function_name(node: exp.Expression) -> str | None:
    type_name = type(node).__name__
    lowered = type_name.lower()
    if lowered.startswith("read") or lowered in {"copy", "currentuser"}:
        return type_name
    if isinstance(node, exp.Anonymous):
        func_name = (node.name or "").lower()
        if func_name in DANGEROUS_FUNCTIONS or func_name.startswith("read_") or func_name.startswith("pg_"):
            return func_name
    return None


def _cte_aliases(tree: exp.Expression) -> set[str]:
    names: set[str] = set()
    with_expr = tree.args.get("with") if hasattr(tree, "args") else None
    if not with_expr:
        return names
    for cte in getattr(with_expr, "expressions", []) or []:
        alias = getattr(cte, "alias", None)
        if alias:
            names.add(str(alias).lower())
    return names


def _column_name(node: exp.Expression | None) -> str:
    if node is None:
        return ""
    if isinstance(node, exp.Column):
        return (node.name or "").lower()
    if isinstance(node, exp.Identifier):
        return (node.name or "").lower()
    return node.sql().lower()


def _time_bounds(select: exp.Select) -> tuple[bool, bool]:
    where = select.args.get("where")
    if where is None:
        return False, False
    has_ge = False
    has_lt = False
    for node in where.walk():
        if isinstance(node, (exp.GTE, exp.GT)) and _column_name(node.this) == "time":
            has_ge = True
        elif isinstance(node, (exp.LTE, exp.LT)) and _column_name(node.this) == "time":
            has_lt = True
        elif isinstance(node, exp.Between) and _column_name(node.this) == "time":
            has_ge = True
            has_lt = True
    return has_ge, has_lt


def _asset_filter_status(select: exp.Select, allowed_assets: list[str]) -> str:
    where = select.args.get("where")
    if where is None:
        return "missing"
    allowed = set(allowed_assets)
    seen = False
    for node in where.walk():
        if isinstance(node, exp.EQ) and _column_name(node.this) == "asset_id":
            seen = True
            value = _literal_value(node.expression)
            if value is None or value not in allowed:
                return "unauthorized"
        elif isinstance(node, exp.In) and _column_name(node.this) == "asset_id":
            seen = True
            values = [_literal_value(item) for item in node.expressions]
            if any(item is None or item not in allowed for item in values):
                return "unauthorized"
    return "ok" if seen else "missing"


def _literal_value(node: exp.Expression | None) -> str | None:
    if isinstance(node, exp.Literal):
        return str(node.this)
    return None


def _sanitize_assets(assets: list[str]) -> list[str]:
    cleaned: list[str] = []
    for asset in assets:
        value = (asset or "").strip()
        if not value or not _ASSET_RE.match(value):
            continue
        if value not in cleaned:
            cleaned.append(value)
    return cleaned


def require_ident(name: str) -> str:
    if not _IDENT_RE.match(name or ""):
        raise SQLSecurityError(f"非法标识符: {name}")
    return name
