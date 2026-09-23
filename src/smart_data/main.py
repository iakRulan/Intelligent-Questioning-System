import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src.smart_data.api.exception_handlers import register_exception_handlers
from src.smart_data.api.v1 import v1_router
from src.smart_data.api.v1.health import live_payload, ready_payload
from src.smart_data.config import settings
from src.smart_data.startup import startup_datasources

os.environ.setdefault("HAYSTACK_TELEMETRY_ENABLED", "False")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    startup_datasources()
    yield


app = FastAPI(
    title="Intelligent Questioning System (智能问数服务)",
    description="燃气轮机健康管理系统时序数据自然语言智能问数（Text2SQL / NL2SQL）核心服务",
    version="0.1.0",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)

register_exception_handlers(app)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.service.cors_origins,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(v1_router)


@app.get("/health/live", tags=["Health"])
async def root_live():
    return live_payload()


@app.get("/health/ready", tags=["Health"])
async def root_ready():
    return ready_payload()


@app.get("/")
async def root():
    from src.smart_data.runtime import query_backend

    return {
        "service": settings.service.name,
        "version": "0.1.0",
        "mode": settings.pipeline.mode,
        "query_backend": query_backend,
        "docs": "/docs",
        "endpoints": {
            "query_sse": "POST /api/v1/nl2sql/query",
            "health_live": "GET /health/live",
            "health_ready": "GET /health/ready",
        },
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "src.smart_data.main:app",
        host=settings.service.host,
        port=settings.service.port,
        reload=True,
    )
