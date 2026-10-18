from contextlib import asynccontextmanager
import json
import logging

from fastapi import FastAPI, Response
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.exc import SQLAlchemyError

from backend.config import PROJECT_ROOT, get_settings
from backend.database import initialize_database
from backend.api.routes import router
from backend.api.backup_routes import router as backup_router
from backend.api.sentence_routes import router as sentence_router
from backend.api.review_routes import router as review_router
from backend.services.maintenance import MaintenanceMiddleware
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
    allow_methods=["GET", "POST", "PUT", "DELETE"],
    allow_headers=["Content-Type", "Authorization"],
    expose_headers=["Retry-After"],
)
app.add_middleware(MaintenanceMiddleware)


@app.get("/api/health")
def health():
    return {"status": "ok", "ocr_provider": settings.ocr_provider, "llm_provider": settings.llm_provider,
            "auth_mode": "guest" if settings.app_mode == "public" else "local"}


@app.exception_handler(OCRUnavailable)
async def ocr_unavailable(request, exc):
    logging.getLogger(__name__).warning("OCR is unavailable: %s", exc)
    return JSONResponse(status_code=503, content={"detail": str(exc)})


@app.exception_handler(RequestValidationError)
async def invalid_request(request, exc):
    # Do not echo raw invalid input. In particular, an escaped lone Unicode
    # surrogate is valid transport JSON but cannot be encoded in a response.
    return Response(status_code=422, media_type="application/json", content=json.dumps({"detail": [
        {"loc": error["loc"], "msg": error["msg"], "type": error["type"]}
        for error in exc.errors()
    ]}, ensure_ascii=True))


@app.exception_handler(SQLAlchemyError)
async def database_error(request, exc):
    logging.getLogger(__name__).error("Database operation failed", exc_info=exc)
    return JSONResponse(status_code=503, content={"detail": "数据库暂时不可用，请稍后重试。"})


@app.exception_handler(Exception)
async def unexpected_error(request, exc):
    logging.getLogger(__name__).error("Unexpected request failure", exc_info=exc)
    return JSONResponse(status_code=500, content={"detail": "处理请求失败，请重试或查看后端日志。"})


app.include_router(router)
app.include_router(backup_router)
app.include_router(sentence_router)
app.include_router(review_router)
frontend_dist = PROJECT_ROOT / "frontend" / "dist"
if frontend_dist.is_dir():
    app.mount("/", StaticFiles(directory=frontend_dist, html=True), name="frontend")
