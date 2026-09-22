import uuid
import asyncio
from typing import Any, AsyncGenerator
from fastapi import APIRouter, Header, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from src.smart_data.domain.query import QueryState
from src.smart_data.pipeline.reporter import StageReporter
from src.smart_data.pipeline.sse import format_sse_frame
from src.smart_data.pipeline.runner import QueryPipelineRunner

router = APIRouter(prefix="/nl2sql", tags=["NL2SQL"])
pipeline_runner = QueryPipelineRunner()


class QueryOptions(BaseModel):
    include_sql: bool = True
    include_raw_preview: bool = True
    raw_preview_rows: int = Field(default=50, ge=1, le=100)


class NL2SQLQueryRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000, description="自然语言查询语句")
    conversation_id: str | None = None
    clarification_id: str | None = None
    clarification_answers: dict[str, Any] = Field(default_factory=dict)
    timezone: str = "Asia/Shanghai"
    locale: str = "zh-CN"
    options: QueryOptions = Field(default_factory=QueryOptions)


async def event_generator(state: QueryState, reporter: StageReporter) -> AsyncGenerator[str, None]:
    # 启动后台管道执行
    task = asyncio.create_task(pipeline_runner.execute(state, reporter))
    try:
        while True:
            event = await reporter.queue.get()
            if event is None:
                break
            yield format_sse_frame(event)
        # 等待后台任务完全结束
        await task
    except asyncio.CancelledError:
        if not task.done():
            task.cancel()
        raise


@router.post("/query")
async def execute_nl2sql_query(
    req: NL2SQLQueryRequest,
    request: Request,
    authorization: str | None = Header(default=None),
    x_request_id: str | None = Header(default=None),
):
    query_id = f"qry_{uuid.uuid4().hex[:12]}"
    trace_id = x_request_id or f"tr_{uuid.uuid4().hex[:16]}"
    user_id = "user_default"  # 模拟已解析认证上下文
    allowed_assets = ["GT-001", "GT-002"]  # 模拟用户机组权限域

    state = QueryState(
        query_id=query_id,
        trace_id=trace_id,
        conversation_id=req.conversation_id,
        user_id=user_id,
        question=req.question,
        allowed_asset_ids=allowed_assets,
    )

    reporter = StageReporter(query_id=query_id, trace_id=trace_id)

    return StreamingResponse(
        event_generator(state, reporter),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
