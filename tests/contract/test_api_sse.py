import pytest
from httpx import AsyncClient, ASGITransport
from src.smart_data.main import app


@pytest.mark.asyncio
async def test_health_endpoints():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. 存活探针
        resp_live = await client.get("/api/v1/health/live")
        assert resp_live.status_code == 200
        assert resp_live.json()["status"] == "ok"

        # 2. 就绪探针
        resp_ready = await client.get("/api/v1/health/ready")
        assert resp_ready.status_code == 200
        assert resp_ready.json()["status"] == "ready"


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
            # 验证返回包含了六步流式事件中的阶段关键事件
            assert "query.accepted" in content
            assert "intent_parsing" in content
            assert "semantic_mapping" in content
            assert "sql_generation" in content
            assert "security_validation" in content
            assert "data_query" in content
            assert "trend_analysis" in content
            assert "result.completed" in content
            assert "stream.end" in content
