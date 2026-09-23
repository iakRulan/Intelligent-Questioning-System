from fastapi import APIRouter

from src.smart_data.config import settings

router = APIRouter(prefix="/health", tags=["Health"])


def live_payload() -> dict:
    return {"status": "ok", "service": settings.service.name}


def ready_payload() -> dict:
    return {
        "status": "ready",
        "service": settings.service.name,
        "environment": settings.service.environment,
        "timezone": settings.service.timezone,
        "mode": settings.pipeline.mode,
        "pipeline_version": settings.pipeline.version,
    }


@router.get("/live")
async def live_check():
    """进程存活探针，不访问外部依赖。"""
    return live_payload()


@router.get("/ready")
async def ready_check():
    """开发阶段就绪探针：配置加载完成即可。"""
    return ready_payload()
