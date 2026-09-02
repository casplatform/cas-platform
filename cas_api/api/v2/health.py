"""Health check endpoints — /api/v2/health, /api/v2/health/sources, /api/v2/health/detailed"""
import time

from fastapi import APIRouter, Response, status
from pydantic import BaseModel

# Uptime is per-process. The engine measures from its own start; this is the
# equivalent for the FastAPI workers, so /api/v2/health/detailed reports its own
# process rather than borrowing a number that means nothing here.
_PROCESS_START = time.time()

from typing import Optional

from core.config import settings
from core.database import health_check
from core.deploy_info import deploy_info


router = APIRouter(tags=["system"])


class HealthResponse(BaseModel):
    status: str
    service: str
    version: str
    environment: str
    database: str
    auth_configured: bool  # AUTH_SECRET yüklü mü
    # Which commit this process is serving. None on a checkout that has never
    # been deployed to -- see core/deploy_info.py.
    commit: Optional[str] = None
    deployed_at: Optional[str] = None


@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    db_ok = health_check()
    _d = deploy_info()
    return HealthResponse(
        status="ok" if db_ok else "degraded",
        service=settings.service_name,
        version=settings.version,
        environment=settings.environment,
        database="ok" if db_ok else "fail",
        auth_configured=bool(settings.auth_secret),
        commit=_d["commit_short"],
        deployed_at=_d["deployed_at"],
    )


# ── ADR 0001, exception 1: the two health endpoints move here ────────────
#
# /health/sources and /health/detailed were 21 lines of routing in the engine
# wrapped around logic that already lived in cas_api. They are served here now,
# from core/system_health.py and core/data_health.py -- the same functions the
# engine's routes call, not copies of them.
#
# The engine's routes still answer during the transition. That is deliberate
# (ADR 0001: "do not delete the losing side in the same release") and it is
# safe precisely because there is one implementation: the two addresses cannot
# report different things.
#
# /health itself does NOT move. watchdog.sh polls http://localhost:8765/health
# every five minutes and scripts/deploy.sh health-checks it on every deploy and
# rollback; it is the engine's liveness probe and belongs to the engine.

@router.get("/health/sources")
async def health_sources() -> dict:
    """Per-source ingestion health, for the portal's staleness banner."""
    try:
        from core.data_health import get_all_health
        return {"sources": get_all_health()}
    except Exception as e:
        # Same shape the engine returns on failure, so a client cannot tell the
        # two apart -- and an empty dict is never dressed up as a clean result.
        return {"sources": {}, "error": "health lookup failed: %s" % str(e)[:120]}


@router.get("/health/detailed")
async def health_detailed(response: Response) -> dict:
    """Per-component system health.

    503 when any component is in error, 200 otherwise -- the same mapping the
    engine applies, so monitoring that watches either address behaves
    identically.
    """
    from core.system_health import check_system_health
    body = check_system_health(start_time=_PROCESS_START, version=settings.version)
    if body.get("status") == "error":
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return body
