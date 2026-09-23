import json
from typing import AsyncGenerator

from src.smart_data.pipeline.reporter import SSEEvent, StageReporter


def format_sse_frame(event: SSEEvent) -> str:
    data_str = json.dumps(event.model_dump(mode="json"), ensure_ascii=False)
    return f"id: {event.seq}\nevent: {event.event}\ndata: {data_str}\n\n"


async def sse_event_stream(reporter: StageReporter) -> AsyncGenerator[str, None]:
    while True:
        event = await reporter.queue.get()
        if event is None:
            break
        yield format_sse_frame(event)
