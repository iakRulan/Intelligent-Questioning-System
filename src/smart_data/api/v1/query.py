import asyncio
import uuid
from contextlib import suppress
from typing import Any, AsyncGenerator

from fastapi import APIRouter, Header, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from src.smart_data.api.dependencies import resolve_auth_context
from src.smart_data.domain.errors import AppError
from src.smart_data.domain.query import QueryState
from src.smart_data.infrastructure.session_store import clarification_store
from src.smart_data.pipeline.reporter import StageReporter
from src.smart_data.pipeline.runner import QueryPipelineRunner
from src.smart_data.pipeline.sse import format_sse_frame

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


def _merge_clarification(req: NL2SQLQueryRequest, user_id: str) -> tuple[str, list[str]]:
    if not req.clarification_id:
        return req.question, []

    context = clarification_store.get(req.clarification_id, user_id)
    if context is None:
        raise AppError("SDQ-409-CLARIFY", "澄清上下文已过期或不匹配", http_status=409)

    allowed_ids = {item["id"] for item in context.questions}
    extra_keys = set(req.clarification_answers) - allowed_ids
    if extra_keys:
        raise AppError("SDQ-409-CLARIFY", "澄清答案与上一轮问题不匹配", http_status=409)

    forced_codes: list[str] = []
    extras: list[str] = []
    for question in context.questions:
        answer = req.clarification_answers.get(question["id"])
        if answer is None or answer == "":
            continue
        if question.get("type") == "single_choice":
            forced_codes.append(str(answer))
        extras.append(str(answer))

    merged_question = context.question
    if extras:
        merged_question = f"{context.question} {' '.join(extras)}"
    return merged_question, forced_codes


async def event_generator(
    state: QueryState,
    reporter: StageReporter,
    request: Request,
) -> AsyncGenerator[str, None]:
    task = asyncio.create_task(pipeline_runner.execute(state, reporter))
    try:
        while True:
            disconnected = False
            with suppress(Exception):
                disconnected = await request.is_disconnected()
            if disconnected:
                if not task.done():
                    task.cancel()
                break
            try:
                event = await asyncio.wait_for(reporter.queue.get(), timeout=1.0)
            except asyncio.TimeoutError:
                if task.done():
                    break
                continue
            if event is None:
                break
            yield format_sse_frame(event)

        while True:
            try:
                event = reporter.queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            if event is None:
                break
            yield format_sse_frame(event)

        if not task.done():
            await task
    except asyncio.CancelledError:
        if not task.done():
            task.cancel()
            with suppress(asyncio.CancelledError, Exception):
                await task
        raise
    finally:
        if not task.done():
            task.cancel()
            with suppress(asyncio.CancelledError, Exception):
                await task


@router.post("/query")
async def execute_nl2sql_query(
    req: NL2SQLQueryRequest,
    request: Request,
    authorization: str | None = Header(default=None),
    x_request_id: str | None = Header(default=None),
):
    auth = resolve_auth_context(authorization)
    question, forced_metric_codes = _merge_clarification(req, auth.user_id)

    query_id = f"qry_{uuid.uuid4().hex[:12]}"
    trace_id = x_request_id or f"tr_{uuid.uuid4().hex[:16]}"

    state = QueryState(
        query_id=query_id,
        trace_id=trace_id,
        conversation_id=req.conversation_id,
        user_id=auth.user_id,
        question=question,
        timezone=req.timezone,
        locale=req.locale,
        allowed_asset_ids=auth.allowed_asset_ids,
        clarification_id=req.clarification_id,
        clarification_answers=req.clarification_answers,
        forced_metric_codes=forced_metric_codes,
        include_sql=req.options.include_sql,
        include_raw_preview=req.options.include_raw_preview,
        raw_preview_rows=req.options.raw_preview_rows,
    )
    reporter = StageReporter(query_id=query_id, trace_id=trace_id)

    return StreamingResponse(
        event_generator(state, reporter, request),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
