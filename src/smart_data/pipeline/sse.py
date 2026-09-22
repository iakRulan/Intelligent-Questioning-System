import json
from typing import AsyncGenerator
from src.smart_data.pipeline.reporter import SSEEvent, StageReporter


def format_sse_frame(event: SSEEvent) -> str:
    """按 Server-Sent Events (SSE) 协议规范格式化单个事件帧。"""
    data_str = json.dumps(event.model_dump(mode="json"), ensure_ascii=False)
    return f"id: {event.seq}\nevent: {event.event}\ndata: {data_str}\n\n"


async def sse_event_stream(reporter: StageReporter) -> AsyncGenerator[str, None]:
    """从 StageReporter 异步消费事件并生成 SSE 文本流。"""
    try:
        while True:
            event = await reporter.queue.get()
            if event is None:
                # 收到结束信号
                break
            yield format_sse_frame(event)
    except Exception:
        # 当连接被客户端意外关闭时中止
        pass
