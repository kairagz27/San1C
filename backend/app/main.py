from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text

from . import auth, ingestion, reconciliation, reports, source
from .db import SessionLocal
from .demo import seed_demo
from .settings import company_timezone

ROOT = Path(__file__).resolve().parents[2]


@asynccontextmanager
async def lifespan(app: FastAPI):
    company_timezone("live")
    with SessionLocal() as db:
        seed_demo(db)
    yield


app = FastAPI(title="San1C", version="0.1.0", lifespan=lifespan,
              docs_url=None, redoc_url=None, openapi_url=None)
for router in (auth.router, ingestion.router, reports.router, source.router, reconciliation.router):
    app.include_router(router)


@app.exception_handler(RequestValidationError)
async def safe_validation(request: Request, exc: RequestValidationError):
    if request.url.path.startswith(("/api/auth/", "/api/live/reconciliation")):
        # Validation responses for these forms must never echo passwords or control file contents.
        return JSONResponse({"detail": "Проверьте обязательные поля и формат данных"}, status_code=422)
    return await request_validation_exception_handler(request, exc)


@app.middleware("http")
async def response_headers(request: Request, call_next):
    response = await call_next(request)
    if request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "same-origin"
    response.headers["X-Frame-Options"] = "DENY"
    return response


@app.get("/api/health")
def health():
    with SessionLocal() as db:
        db.execute(text("SELECT 1"))
    return {"status": "ok"}


@app.get("/guide/1c")
def adapter_guide():
    return FileResponse(ROOT / "integrations/1c/README.md", media_type="text/plain; charset=utf-8")


if (ROOT / "frontend/dist").is_dir():
    app.mount("/", StaticFiles(directory=ROOT / "frontend/dist", html=True), name="web")
