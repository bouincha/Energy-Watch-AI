"""FastAPI entry point: API + dashboard + observability middleware."""

from __future__ import annotations

import logging
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app import __version__
from app.api.routes import router
from app.config import get_settings
from app.core.observability import (
    configure_logging,
    log_event,
    metrics,
    reset_correlation_id,
    sanitize_correlation_id,
    set_correlation_id,
)
from app.data.energy_data import get_dataset
from app.llm.gateway import get_gateway
from app.rag.retriever import get_retriever

STATIC_DIR = Path(__file__).parent / "static"
logger = logging.getLogger("energywatch.http")


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    configure_logging(settings.log_level)
    ds = get_dataset()
    get_retriever()
    gw = get_gateway()
    log_event(logger, "EnergyWatch AI started", version=__version__, environment=settings.environment,
              llm_provider=gw.provider, llm_model=gw.model_name, meters=len(ds.series),
              anomalies=len(ds.anomalies()))
    yield


app = FastAPI(
    title="EnergyWatch AI",
    version=__version__,
    description="Multi-agent energy monitoring platform (LangGraph). Every response carries an "
                "`X-Correlation-ID` header that links logs, agent spans, incidents and audit entries.",
    lifespan=lifespan,
)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["GET", "POST"],
                   allow_headers=["*"], expose_headers=["X-Correlation-ID"])


@app.middleware("http")
async def correlation_middleware(request: Request, call_next):
    cid = sanitize_correlation_id(request.headers.get("x-correlation-id"))
    token = set_correlation_id(cid)
    start = time.perf_counter()
    path = request.url.path
    route = path if not path.startswith("/static") else "/static"
    try:
        response = await call_next(request)
    except Exception:
        logger.exception("unhandled error")
        response = JSONResponse({"detail": "Internal server error", "correlation_id": cid}, status_code=500)
    finally:
        reset_correlation_id(token)
    duration = (time.perf_counter() - start) * 1000
    response.headers["X-Correlation-ID"] = cid
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    if not route.startswith(("/static", "/health")):
        metrics.inc("energywatch_http_requests_total", route=route if route.startswith("/api") else "other",
                    method=request.method, status=str(response.status_code))
        logger.info(f"{request.method} {path} {response.status_code}",
                    extra={"correlation_id": cid, "extra_fields": {"duration_ms": round(duration, 1),
                                                                   "status": response.status_code}})
    return response


app.include_router(router)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/", include_in_schema=False)
def dashboard() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")
