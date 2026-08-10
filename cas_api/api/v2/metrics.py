"""Public metrics endpoint — /api/v2/metrics/*

PUBLIC: Auth gerekmez (şeffaflık iddiası).
GUVENLI: Hiçbir sensitive data döndürmez.
"""
from fastapi import APIRouter

from services.metrics import get_cycle_time_metrics


router = APIRouter(prefix="/metrics", tags=["metrics"])


@router.get("/cycle-time")
async def cycle_time():
    """Decision cycle time metric — savunulabilir, ECSS-traceable.

    Public endpoint, 5 dakika cache.

    Veri:
    - cycle_time: claim + industry baseline + speedup
    - components: 3 ana pipeline aşaması (CDM/Scan/Alert)
    - evidence: 24 saatlik gözlem penceresi (count'lar agregat)
    - methodology: kaynak + ECSS uyumu

    Asla dönmez: PIDs, hostnames, user counts, sat counts, NORAD IDs,
    conjunction counts (raw), raw log lines.
    """
    return get_cycle_time_metrics()
