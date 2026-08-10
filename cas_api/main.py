"""CAS API — FastAPI application entry point."""
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from core.config import settings
from core.database import init_pool, close_pool
from api.v2 import health, notifications, metrics, ml, maneuver, launch, ws, mission, vleo, reports, insurance, support


@asynccontextmanager
async def lifespan(app: FastAPI):
    print(f"[{settings.service_name}] Başlatılıyor... (env={settings.environment})", flush=True)
    init_pool(min_size=2, max_size=10)
    if not settings.auth_secret:
        print(f"[{settings.service_name}] ⚠️  AUTH_SECRET yüklenmedi (auth endpoint'ler çalışmaz)", flush=True)
    else:
        print(f"[{settings.service_name}] AUTH_SECRET yüklü (len={len(settings.auth_secret)})", flush=True)
    print(f"[{settings.service_name}] DB pool hazır.", flush=True)
    yield
    print(f"[{settings.service_name}] Kapatılıyor...", flush=True)
    close_pool()


app = FastAPI(
    title="CAS Platform API",
    description="Conjunction Decision Support — REST API v2",
    version=settings.version,
    docs_url="/api/v2/docs",
    redoc_url="/api/v2/redoc",
    openapi_url="/api/v2/openapi.json",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)

# Routers
app.include_router(health.router, prefix="/api/v2")
app.include_router(notifications.router, prefix="/api/v2")
app.include_router(metrics.router, prefix="/api/v2")
app.include_router(ml.router, prefix="/api/v2")
app.include_router(maneuver.router, prefix="/api/v2")
app.include_router(launch.router, prefix="/api/v2")
app.include_router(ws.router, prefix="/api/v2")
app.include_router(mission.router, prefix="/api/v2")
app.include_router(vleo.router, prefix="/api/v2")
app.include_router(support.router, prefix="/api/v2")
app.include_router(reports.router, prefix="/api/v2")
app.include_router(insurance.router, prefix="/api/v2")


@app.get("/")
async def root():
    return {
        "service": settings.service_name,
        "version": settings.version,
        "docs": "/api/v2/docs",
    }
