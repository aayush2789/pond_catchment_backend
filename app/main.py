import json
import logging
import time
import uuid

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from pathlib import Path
from fastapi.staticfiles import StaticFiles
from app.api.v1.api import api_router
from app.core.config import settings

app = FastAPI(
    title=settings.PROJECT_NAME,
    version=settings.VERSION,
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
)

# --- Structured request logging (request ID, node, status, latency) ---------------

logging.basicConfig(
    level=getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO),
    format="%(message)s",
)
request_logger = logging.getLogger("pond.request")


class JSONFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        if isinstance(record.msg, dict):
            return json.dumps(record.msg, ensure_ascii=False)
        return super().format(record)


_handler = logging.StreamHandler()
_handler.setFormatter(JSONFormatter())
request_logger.handlers = [_handler]
request_logger.propagate = False


@app.middleware("http")
async def request_context_middleware(request: Request, call_next):
    """Attach a request ID (from the proxy or generated), log latency/status.

    The request ID lets one request be traced from Nginx through the selected
    FastAPI node; the node id makes load-balancing distribution observable.
    """
    from app.services.cache import default_node_id

    request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex[:16]
    started = time.perf_counter()
    response = await call_next(request)
    latency_ms = round((time.perf_counter() - started) * 1000.0, 2)
    response.headers["X-Request-ID"] = request_id
    response.headers["X-Served-By"] = default_node_id()
    request_logger.info(
        {
            "event": "request",
            "request_id": request_id,
            "node": default_node_id(),
            "method": request.method,
            "path": request.url.path,
            "status": response.status_code,
            "latency_ms": latency_ms,
        }
    )
    return response


app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router, prefix=settings.API_V1_STR)

# Frontend (Phase 9): lightweight static map application served by the same service.
_STATIC_DIR = Path(__file__).resolve().parent / "static"
if _STATIC_DIR.exists():
    app.mount("/app", StaticFiles(directory=str(_STATIC_DIR), html=True), name="frontend")


@app.get("/", tags=["Root"])
def root_endpoint():
    return {
        "project": settings.PROJECT_NAME,
        "version": settings.VERSION,
        "docs": "/docs",
        "health": f"{settings.API_V1_STR}/health",
        "frontend": "/app/",
    }
