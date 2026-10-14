from contextlib import asynccontextmanager
import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.exc import SQLAlchemyError

from backend.config import PROJECT_ROOT, get_settings
from backend.database import initialize_database
from backend.api.routes import router
from backend.ocr.service import OCRUnavailable


@asynccontextmanager
async def lifespan(app: FastAPI):
    initialize_database()
    yield


app = FastAPI(title="LexiRead API", version="0.1.0", lifespan=lifespan)
settings = get_settings()
app.add_middleware(
    CORSMiddleware,
    allow_origins=[origin.strip() for origin in settings.cors_origins.split(",") if origin.strip()],
    allow_methods=["GET", "POST", "PUT"],
    allow_headers=["Content-Type"],
)


@app.get("/api/health")
def health():
    return {"status": "ok", "ocr_provider": settings.ocr_provider, "llm_provider": settings.llm_provider}


@app.exception_handler(OCRUnavailable)
async def ocr_unavailable(request, exc):
    logging.getLogger(__name__).warning("OCR is unavailable: %s", exc)
    return JSONResponse(status_code=503, content={"detail": str(exc)})


@app.exception_handler(SQLAlchemyError)
async def database_error(request, exc):
    logging.getLogger(__name__).error("Database operation failed", exc_info=exc)
    return JSONResponse(status_code=503, content={"detail": "数据库暂时不可用，请稍后重试。"})


@app.exception_handler(Exception)
async def unexpected_error(request, exc):
    logging.getLogger(__name__).error("Unexpected request failure", exc_info=exc)
    return JSONResponse(status_code=500, content={"detail": "处理请求失败，请重试或查看后端日志。"})


app.include_router(router)
frontend_dist = PROJECT_ROOT / "frontend" / "dist"
if frontend_dist.is_dir():
    app.mount("/", StaticFiles(directory=frontend_dist, html=True), name="frontend")
