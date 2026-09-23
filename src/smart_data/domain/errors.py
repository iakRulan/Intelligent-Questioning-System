class AppError(Exception):
    """可映射到设计文档业务码的应用异常。"""

    def __init__(self, error_code: str, message: str, http_status: int = 400):
        super().__init__(message)
        self.error_code = error_code
        self.message = message
        self.http_status = http_status


class SQLSecurityError(Exception):
    """SQL 违背安全规则异常。"""
