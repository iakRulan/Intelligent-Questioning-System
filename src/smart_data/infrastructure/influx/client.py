from __future__ import annotations

from typing import Any

import httpx

from src.smart_data.config import settings


class InfluxHttpClient:
    """InfluxDB v3 HTTP SQL 查询客户端。Flight/gRPC 在部分穿透环境下不可用，统一走 /api/v3/query_sql。"""

    def __init__(
        self,
        host: str | None = None,
        token: str | None = None,
        database: str | None = None,
        timeout_seconds: float | None = None,
    ):
        self.host = (host or settings.influxdb.host).rstrip("/")
        self.token = token or settings.influxdb.token
        self.database = database or settings.influxdb.database
        self.timeout_seconds = timeout_seconds or settings.influxdb.timeout_seconds
        self.query_path = "/api/v3/query_sql"

    def ping(self) -> bool:
        try:
            response = httpx.get(
                f"{self.host}/health",
                headers=self._headers(),
                timeout=min(self.timeout_seconds, 5.0),
            )
            return response.status_code == 200
        except Exception:
            return False

    def query(self, sql: str) -> list[dict[str, Any]]:
        response = httpx.post(
            f"{self.host}{self.query_path}",
            headers=self._headers(),
            json={"db": self.database, "q": sql},
            timeout=self.timeout_seconds,
        )
        if response.status_code >= 400:
            raise RuntimeError(f"InfluxDB 查询失败 HTTP {response.status_code}: {response.text[:300]}")
        payload = response.json()
        if isinstance(payload, list):
            return payload
        if isinstance(payload, dict) and "data" in payload:
            return payload["data"]
        raise RuntimeError("InfluxDB 返回了无法识别的结果结构")

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if self.token and self.token not in {"token_placeholder", "EMPTY"}:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers
