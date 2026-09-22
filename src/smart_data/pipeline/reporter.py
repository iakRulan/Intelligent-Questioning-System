import asyncio
from datetime import datetime, timezone
from typing import Any
from pydantic import BaseModel, Field


class SSEEvent(BaseModel):
    query_id: str
    trace_id: str
    seq: int
    event: str
    stage: str | None = None
    status: str = "running"
    timestamp: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    progress: int = 0
    payload: dict[str, Any] = Field(default_factory=dict)


class StageReporter:
    """异步有界事件报告器，负责将管道内部状态转化为严格契约的 SSE 事件。"""

    STAGE_PROGRESS = {
        "intent_parsing": 15,
        "semantic_mapping": 35,
        "sql_generation": 55,
        "security_validation": 70,
        "data_query": 85,
        "trend_analysis": 100,
    }

    def __init__(self, query_id: str, trace_id: str, queue_size: int = 100):
        self.query_id = query_id
        self.trace_id = trace_id
        self.queue: asyncio.Queue[SSEEvent | None] = asyncio.Queue(maxsize=queue_size)
        self._seq = 0

    def _next_seq(self) -> int:
        self._seq += 1
        return self._seq

    async def emit(
        self,
        event_type: str,
        stage: str | None = None,
        status: str = "running",
        payload: dict[str, Any] | None = None,
        progress: int | None = None,
    ) -> None:
        if progress is None and stage in self.STAGE_PROGRESS:
            progress = self.STAGE_PROGRESS[stage]
        elif progress is None:
            progress = 0

        event = SSEEvent(
            query_id=self.query_id,
            trace_id=self.trace_id,
            seq=self._next_seq(),
            event=event_type,
            stage=stage,
            status=status,
            progress=progress,
            payload=payload or {},
        )
        await self.queue.put(event)

    async def accepted(self) -> None:
        await self.emit("query.accepted", status="accepted", progress=5)

    async def stage_started(self, stage: str, summary: dict[str, Any] | None = None) -> None:
        await self.emit("stage.started", stage=stage, status="running", payload=summary or {})

    async def stage_completed(self, stage: str, payload: dict[str, Any] | None = None) -> None:
        await self.emit("stage.completed", stage=stage, status="completed", payload=payload or {})

    async def stage_failed(self, stage: str, error_code: str, message: str) -> None:
        await self.emit(
            "stage.failed",
            stage=stage,
            status="failed",
            payload={"error_code": error_code, "message": message},
        )

    async def clarification_required(
        self, stage: str, questions: list[dict[str, Any]], clarification_id: str
    ) -> None:
        await self.emit(
            "clarification.required",
            stage=stage,
            status="clarification",
            payload={
                "clarification_id": clarification_id,
                "questions": questions,
            },
        )

    async def result_completed(self, payload: dict[str, Any]) -> None:
        await self.emit("result.completed", stage="trend_analysis", status="completed", progress=100, payload=payload)

    async def query_cancelled(self, reason: str = "Client disconnected") -> None:
        await self.emit("query.cancelled", status="cancelled", payload={"reason": reason})

    async def stream_end(self) -> None:
        await self.emit("stream.end", status="finished", progress=100)
        # 发送 None 作为结束信号
        await self.queue.put(None)
