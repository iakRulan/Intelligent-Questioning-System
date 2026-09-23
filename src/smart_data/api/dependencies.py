from pydantic import BaseModel, Field

from src.smart_data.config import settings
from src.smart_data.domain.errors import AppError

DEFAULT_DEV_ASSETS = ["GT-001", "GT-002"]


class AuthContext(BaseModel):
    user_id: str
    allowed_asset_ids: list[str] = Field(default_factory=list)


def resolve_auth_context(authorization: str | None) -> AuthContext:
    """解析认证上下文。权限域只来自服务端，不接受请求体中的自称权限。"""
    is_production = settings.service.environment == "production"
    if not authorization:
        if is_production:
            raise AppError("SDQ-401-AUTH", "未认证", http_status=401)
        return AuthContext(user_id="user_default", allowed_asset_ids=list(DEFAULT_DEV_ASSETS))

    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise AppError("SDQ-401-AUTH", "未认证", http_status=401)

    token = token.strip()
    if token.startswith("dev:"):
        parts = token.split(":")
        user_id = parts[1].strip() if len(parts) > 1 and parts[1].strip() else "user_default"
        if len(parts) > 2 and parts[2].strip():
            assets = [item.strip() for item in parts[2].split(",") if item.strip()]
        else:
            assets = list(DEFAULT_DEV_ASSETS)
        if not assets:
            raise AppError("SDQ-403-SCOPE", "无机组访问权限", http_status=403)
        return AuthContext(user_id=user_id, allowed_asset_ids=assets)

    if is_production:
        raise AppError("SDQ-401-AUTH", "令牌无效", http_status=401)
    return AuthContext(user_id="user_default", allowed_asset_ids=list(DEFAULT_DEV_ASSETS))
