from fastapi import APIRouter

from app.core.config import settings
from app.schemas.health import HealthResponse
from app.services.cache import CacheLayer, default_node_id

router = APIRouter()


@router.get("/health", response_model=HealthResponse)
def get_health() -> HealthResponse:
    """Lightweight liveness probe — must never call DEM/rainfall/hydrology code."""
    return HealthResponse(
        status="healthy",
        project_name=settings.PROJECT_NAME,
        version=settings.VERSION,
    )


@router.get("/ready")
def get_ready() -> dict:
    """Readiness probe for the load balancer.

    The application serves traffic even when the shared Redis cache is down
    (caches degrade gracefully), so readiness reflects the API process itself;
    component states are reported for diagnostics.
    """
    redis_state = "up" if CacheLayer.is_available() else ("disabled" if not settings.REDIS_URL else "down")
    return {
        "status": "ready",
        "node": default_node_id(),
        "environment": settings.APP_ENV,
        "redis": redis_state,
    }


@router.get("/version")
def get_version() -> dict:
    """Build metadata for verifying that all nodes run the same deployment.

    Deliberately exposes only safe metadata — never secrets or credentials.
    """
    return {
        "project": settings.PROJECT_NAME,
        "version": settings.VERSION,
        "git_commit": settings.GIT_COMMIT or "unknown",
        "node": default_node_id(),
        "environment": settings.APP_ENV,
    }
