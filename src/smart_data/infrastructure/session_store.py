from datetime import datetime, timedelta, timezone
from typing import Any

from pydantic import BaseModel, Field


class ClarificationContext(BaseModel):
    clarification_id: str
    user_id: str
    question: str
    timezone: str = "Asia/Shanghai"
    locale: str = "zh-CN"
    allowed_asset_ids: list[str] = Field(default_factory=list)
    stage: str
    questions: list[dict[str, Any]] = Field(default_factory=list)
    expires_at: datetime
    forced_metric_codes: list[str] = Field(default_factory=list)


class ClarificationStore:
    """进程内澄清上下文。过期或用户不匹配时按同一结果处理，避免泄露。"""

    def __init__(self, ttl_seconds: int = 600):
        self._ttl = ttl_seconds
        self._items: dict[str, ClarificationContext] = {}

    @property
    def ttl_seconds(self) -> int:
        return self._ttl

    def put(self, ctx: ClarificationContext) -> None:
        self._items[ctx.clarification_id] = ctx

    def get(self, clarification_id: str, user_id: str) -> ClarificationContext | None:
        ctx = self._items.get(clarification_id)
        if ctx is None or ctx.user_id != user_id:
            return None
        now = datetime.now(timezone.utc)
        expires = ctx.expires_at
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=timezone.utc)
        if now >= expires:
            self._items.pop(clarification_id, None)
            return None
        return ctx

    def drop(self, clarification_id: str) -> None:
        self._items.pop(clarification_id, None)

    def expires_at(self, now: datetime | None = None) -> datetime:
        return (now or datetime.now(timezone.utc)) + timedelta(seconds=self._ttl)


clarification_store = ClarificationStore(ttl_seconds=600)
