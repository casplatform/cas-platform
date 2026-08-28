"""Health check endpoint — /api/v2/health"""
from fastapi import APIRouter
from pydantic import BaseModel

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
