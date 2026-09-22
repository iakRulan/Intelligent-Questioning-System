import re
from typing import Any
import sqlglot
from sqlglot import exp
from src.smart_data.config import settings


class SQLSecurityError(Exception):
    """SQL 违背安全规则异常。"""
    pass


class SQLGuard:
    """基于 sqlglot AST 的只读 SQL 安全网关与改写器。

    确保任何发往时序数据库的 SQL 语句严格受控：
    1. 阻断一切 DDL/DML 写操作与管理命令；
    2. 禁止通配符 SELECT *；
    3. 强制校验/注入时间范围谓词；
    4. 强制注入并限制 LIMIT <= max_result_rows；
    5. 白名单校验表与字段。
    """

    FORBIDDEN_EXPRESSIONS = (
        exp.Insert,
        exp.Update,
        exp.Delete,
        exp.Create,
        exp.Drop,
        exp.Alter,
        exp.Command,
    )

    DANGEROUS_FUNCTIONS = {
        "read_csv",
        "read_parquet",
        "read_json",
        "load",
        "sleep",
        "version",
        "current_user",
        "system_user",
    }

    def __init__(
        self,
        allowed_tables: set[str] | None = None,
        allowed_columns: set[str] | None = None,
        max_rows: int | None = None,
    ):
        self.allowed_tables = allowed_tables or set()
        self.allowed_columns = allowed_columns or set()
        self.max_rows = max_rows or settings.query_policy.max_result_rows

    def validate_and_rewrite(
        self,
        raw_sql: str,
        start_time_iso: str | None = None,
        end_time_iso: str | None = None,
        enforce_whitelist: bool = False,
    ) -> tuple[bool, str, list[str]]:
        """对输入的原始 SQL 执行 AST 深度静态校验与安全改写。

        Returns:
            (is_safe, rewritten_safe_sql, reasons)
        """
        reasons: list[str] = []
        clean_sql = raw_sql.strip()

        # 1. 拦截注释注入与分号多语句
        if ";" in clean_sql.rstrip(";"):
            return False, "", ["拒绝多语句执行 (Multiple SQL statements detected)"]

        # 去除末尾分号
        clean_sql = clean_sql.rstrip(";").strip()

        # 2. AST 解析
        try:
            parsed = sqlglot.parse(clean_sql)
        except Exception as e:
            return False, "", [f"SQL 语法解析失败: {str(e)}"]

        if not parsed or len(parsed) != 1 or parsed[0] is None:
            return False, "", ["无效的 SQL 语句结构"]

        tree = parsed[0]

        # 3. 根节点只允许 SELECT 或 SELECT CTE (WITH)
        if isinstance(tree, exp.Select):
            root_select = tree
        elif isinstance(tree, exp.With):
            # 必须最终指向 SELECT
            if not isinstance(tree.this, exp.Select):
                return False, "", ["CTE 表达式必须最终输出 SELECT"]
            root_select = tree.this
        else:
            return False, "", [f"非法根语句类型: {tree.__class__.__name__}，仅允许只读 SELECT 语句"]

        # 4. 深度遍历，阻断所有禁止的操作类型
        for node in tree.walk():
            if isinstance(node, self.FORBIDDEN_EXPRESSIONS):
                return False, "", [f"检测到高危或非只读操作: {node.__class__.__name__}"]

            # 拦截危险函数
            if isinstance(node, exp.Anonymous):
                func_name = node.name.lower()
                if func_name in self.DANGEROUS_FUNCTIONS:
                    return False, "", [f"禁止调用危险函数: {func_name}"]

        # 5. 禁止通配符 SELECT *
        has_star = False
        for select_expr in root_select.expressions:
            if isinstance(select_expr, exp.Star):
                has_star = True
                break
        if has_star:
            return False, "", ["禁止使用 SELECT * 通配符全量拉取，必须指明具体列名"]

        # 6. 表名与列名白名单校验（若开启）
        if enforce_whitelist:
            for table in tree.find_all(exp.Table):
                table_name = table.name.lower()
                if self.allowed_tables and table_name not in self.allowed_tables:
                    reasons.append(f"未授权或不存在的测量表: {table_name}")

            for col in tree.find_all(exp.Column):
                col_name = col.name.lower()
                if col_name != "time" and self.allowed_columns and col_name not in self.allowed_columns:
                    reasons.append(f"未授权或不存在的列名: {col_name}")

            if reasons:
                return False, "", reasons

        # 7. 注入或校验 LIMIT
        limit_node = root_select.args.get("limit")
        if limit_node is None:
            # 自动注入 LIMIT
            root_select = root_select.limit(self.max_rows)
        else:
            try:
                curr_limit = int(limit_node.expression.this)
                if curr_limit > self.max_rows:
                    # 裁剪到策略上限
                    limit_node.set("expression", exp.Literal.number(self.max_rows))
                    reasons.append(f"LIMIT 值超出策略上限，已自动收敛至 {self.max_rows}")
            except Exception:
                limit_node.set("expression", exp.Literal.number(self.max_rows))

        # 8. 校验与注入时间谓词
        sql_text = tree.sql()
        has_time_filter = "time" in sql_text.lower() and (">" in sql_text or "<" in sql_text or "between" in sql_text.lower())

        if not has_time_filter and start_time_iso and end_time_iso:
            time_clause = f"time >= '{start_time_iso}' AND time < '{end_time_iso}'"
            root_select = root_select.where(time_clause)
            reasons.append("原始语句未包含时间范围，已自动注入指定时间窗口约束")
        elif not has_time_filter and not (start_time_iso and end_time_iso):
            return False, "", ["时序数据库查询必须包含明确的时间谓词 (time filter)"]

        # 重新生成干净的标准 SQL
        rewritten_sql = root_select.sql()
        return True, rewritten_sql, reasons
