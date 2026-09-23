from fastapi import APIRouter

from src.smart_data.config import settings
from src.smart_data.runtime import query_backend

router = APIRouter(prefix="/health", tags=["Health"])


def live_payload() -> dict:
    return {"status": "ok", "service": settings.service.name}


def ready_payload() -> dict:
    checks = {
        "mysql": False,
        "influx": False,
    }
    if query_backend == "mysql" or settings.pipeline.mode in {"mysql", "auto"}:
        try:
            from src.smart_data.infrastructure.mysql.engine import ping_mysql

            checks["mysql"] = ping_mysql()
        except Exception:
            checks["mysql"] = False
    if query_backend == "influx" or settings.pipeline.mode in {"influx", "auto"}:
        try:
            from src.smart_data.infrastructure.influx.client import InfluxHttpClient

            checks["influx"] = InfluxHttpClient().ping()
        except Exception:
            checks["influx"] = False

    ready = query_backend == "mock" or any(checks.values())
    return {
        "status": "ready" if ready else "degraded",
        "service": settings.service.name,
        "environment": settings.service.environment,
        "timezone": settings.service.timezone,
        "mode": settings.pipeline.mode,
        "query_backend": query_backend,
        "pipeline_version": settings.pipeline.version,
        "checks": checks,
    }


@router.get("/live")
async def live_check():
    """进程存活探针，不访问外部依赖。"""
    return live_payload()


@router.get("/ready")
async def ready_check():
    return ready_payload()
