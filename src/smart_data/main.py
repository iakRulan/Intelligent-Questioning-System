from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from src.smart_data.config import settings
from src.smart_data.api.v1 import v1_router

app = FastAPI(
    title="Intelligent Questioning System (智能问数服务)",
    description="燃气轮机健康管理系统时序数据自然语言智能问数（Text2SQL / NL2SQL）核心服务",
    version="0.1.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

# 跨域设置
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 注册 API 路由
app.include_router(v1_router)


@app.get("/")
async def root():
    return {
        "service": settings.service.name,
        "version": "0.1.0",
        "docs": "/docs",
        "endpoints": {
            "query_sse": "POST /api/v1/nl2sql/query",
            "health_live": "GET /api/v1/health/live",
            "health_ready": "GET /api/v1/health/ready",
        },
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("src.smart_data.main:app", host=settings.service.host, port=settings.service.port, reload=True)
