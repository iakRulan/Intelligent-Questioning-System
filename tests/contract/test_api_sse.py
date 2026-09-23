import json

import pytest
from httpx import ASGITransport, AsyncClient

from src.smart_data.main import app


def _parse_sse(chunks: list[str]) -> list[dict]:
    events: list[dict] = []
    current_event = None
    current_data = None
    for line in chunks:
        if line.startswith("event:"):
            current_event = line.split(":", 1)[1].strip()
        elif line.startswith("data:"):
            current_data = json.loads(line.split(":", 1)[1].strip())
            events.append({"event": current_event, "data": current_data})
            current_event = None
            current_data = None
    return events


@pytest.mark.asyncio
async def test_health_endpoints():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp_live = await client.get("/api/v1/health/live")
        assert resp_live.status_code == 200
        assert resp_live.json()["status"] == "ok"

        resp_ready = await client.get("/api/v1/health/ready")
        assert resp_ready.status_code == 200
        assert resp_ready.json()["status"] == "ready"

        root_live = await client.get("/health/live")
        assert root_live.status_code == 200
        assert root_live.json()["status"] == "ok"


@pytest.mark.asyncio
async def test_nl2sql_query_sse_stream():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        payload = {
            "question": "帮我查询 GT-001 今天的平均排气温度",
            "timezone": "Asia/Shanghai",
        }
        async with client.stream("POST", "/api/v1/nl2sql/query", json=payload) as resp:
            assert resp.status_code == 200
            assert "text/event-stream" in resp.headers["content-type"]

            chunks = []
            async for line in resp.aiter_lines():
                if line:
                    chunks.append(line)

            content = "\n".join(chunks)
            assert "query.accepted" in content
            assert "intent_parsing" in content
            assert "semantic_mapping" in content
            assert "sql_generation" in content
            assert "security_validation" in content
            assert "data_query" in content
            assert "trend_analysis" in content
            assert "result.completed" in content
            assert "stream.end" in content

            events = _parse_sse(chunks)
            seqs = [item["data"]["seq"] for item in events]
            assert seqs == sorted(seqs)
            assert seqs == list(range(1, len(seqs) + 1))
            assert events[-1]["event"] == "stream.end"


@pytest.mark.asyncio
async def test_nl2sql_clarification_ends_stream():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        payload = {"question": "查一下排温", "timezone": "Asia/Shanghai"}
        async with client.stream("POST", "/api/v1/nl2sql/query", json=payload) as resp:
            assert resp.status_code == 200
            chunks = []
            async for line in resp.aiter_lines():
                if line:
                    chunks.append(line)
            content = "\n".join(chunks)
            assert "clarification.required" in content
            assert "stream.end" in content
            assert "result.completed" not in content

            events = _parse_sse(chunks)
            clarify = next(item for item in events if item["event"] == "clarification.required")
            clarification_id = clarify["data"]["payload"]["clarification_id"]
            assert clarification_id

        follow_up = {
            "question": "查一下排温",
            "timezone": "Asia/Shanghai",
            "clarification_id": clarification_id,
            "clarification_answers": {
                "q_0": "今天",
                "q_1": "GT-001",
            },
        }
        async with client.stream("POST", "/api/v1/nl2sql/query", json=follow_up) as resp:
            chunks = []
            async for line in resp.aiter_lines():
                if line:
                    chunks.append(line)
            content = "\n".join(chunks)
            assert "result.completed" in content
            assert "stream.end" in content


@pytest.mark.asyncio
async def test_invalid_clarification_returns_409():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/nl2sql/query",
            json={
                "question": "查一下排温",
                "clarification_id": "clarify_not_exist",
                "clarification_answers": {"q_0": "今天"},
            },
        )
        assert resp.status_code == 409
        assert resp.json()["error_code"] == "SDQ-409-CLARIFY"
