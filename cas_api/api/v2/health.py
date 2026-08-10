"""Health check endpoint — /api/v2/health"""
from fastapi import APIRouter
from pydantic import BaseModel

from core.config import settings
from core.database import health_check


router = APIRouter(tags=["system"])


class HealthResponse(BaseModel):
    status: str
    service: str
    version: str
    environment: str
    database: str
    auth_configured: bool  # AUTH_SECRET yüklü mü


@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    db_ok = health_check()
    return HealthResponse(
        status="ok" if db_ok else "degraded",
        service=settings.service_name,
        version=settings.version,
        environment=settings.environment,
        database="ok" if db_ok else "fail",
        auth_configured=bool(settings.auth_secret),
    )
