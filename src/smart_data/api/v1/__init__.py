from fastapi import APIRouter
from src.smart_data.api.v1.query import router as query_router
from src.smart_data.api.v1.health import router as health_router

v1_router = APIRouter(prefix="/api/v1")
v1_router.include_router(query_router)
v1_router.include_router(health_router)

__all__ = ["v1_router"]
