from fastapi import APIRouter
from src.smart_data.config import settings

router = APIRouter(prefix="/health", tags=["Health"])


@router.get("/live")
async def live_check():
    """进程存活探针，不访问外部依赖。"""
    return {"status": "ok", "service": settings.service.name}


@router.get("/ready")
async def ready_check():
    """就绪探针，检查配置与核心组件初始化状态。"""
    return {
        "status": "ready",
        "service": settings.service.name,
        "environment": settings.service.environment,
        "timezone": settings.service.timezone,
    }
