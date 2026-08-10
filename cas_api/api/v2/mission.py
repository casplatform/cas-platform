"""Mission Design endpoints — /api/v2/mission/* (Sprint #10).

Collision-aware orbit comparison for the design phase. Decision support:
compares candidate orbits across catalog density, debris fraction, empirical
conjunction history, and orbital lifetime — the operator weighs the trade-offs.
"""
import logging
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator

from core.auth import get_current_user, CurrentUser, require_feature
from services import mission_design as md

log = logging.getLogger(__name__)
router = APIRouter(tags=["mission-design"])


class OrbitCandidate(BaseModel):
    altitude_km: float = Field(..., ge=150.0, le=2000.0,
                               description="Circular orbit altitude (km), LEO range")
    inclination_deg: Optional[float] = Field(
        default=None, ge=0.0, le=180.0,
        description="Inclination (deg). If given, density is gated to crossing-"
                    "relevant inclinations; if omitted, the whole altitude band.")
    label: Optional[str] = Field(default=None, max_length=80)


class OrbitCompareRequest(BaseModel):
    candidates: List[OrbitCandidate] = Field(..., min_length=1, max_length=6,
                                             description="2-6 candidate orbits to compare")
    band_half_km: float = Field(default=25.0, ge=5.0, le=100.0,
                                description="Altitude band half-width (km)")
    inclination_tolerance_deg: float = Field(default=5.0, ge=1.0, le=20.0)
    ballistic_coefficient: float = Field(default=50.0, ge=5.0, le=500.0,
                                         description="B* proxy for lifetime estimate")
    f107_flux: float = Field(default=150.0, ge=60.0, le=300.0,
                             description="Solar F10.7 flux for lifetime estimate")

    @field_validator("candidates")
    @classmethod
    def _at_least_two_for_comparison(cls, v):
        # one candidate is allowed (single-orbit assessment) but comparison
        # is the intended use; we don't hard-fail on one.
        return v


@router.post("/mission/orbit-compare")
async def orbit_compare(req: OrbitCompareRequest,
                        user: CurrentUser = Depends(require_feature("mission_design_access"))):
    """Compare candidate orbits across four data-backed dimensions.

    Returns per-candidate: catalog density (type-split), debris fraction,
    historical conjunction frequency (+ debris-involved %), orbital lifetime/
    regime, and a qualitative congestion label. No single collapsed score —
    the operator weighs trade-offs (lifetime vs debris exposure) themselves.
    """
    try:
        candidates = [
            {"altitude_km": c.altitude_km,
             "inclination_deg": c.inclination_deg,
             "label": c.label}
            for c in req.candidates
        ]
        result = md.compare_orbits(
            candidates,
            band_half_km=req.band_half_km,
            inc_tol_deg=req.inclination_tolerance_deg,
            ballistic_coef=req.ballistic_coefficient,
            f107_flux=req.f107_flux,
        )
        return result
    except Exception as e:
        log.exception("orbit-compare failed")
        raise HTTPException(status_code=500, detail=f"orbit comparison failed: {type(e).__name__}")


@router.get("/mission/info")
async def mission_info(user: CurrentUser = Depends(get_current_user)):
    """Describe the Mission Design capability and its honest boundaries."""
    return {
        "capability": "Collision-aware orbit comparison (design phase)",
        "endpoint": "/api/v2/mission/orbit-compare",
        "dimensions": [
            "catalog density (type-separated: debris/rocket_body/payload)",
            "debris fraction (non-maneuverable threat %)",
            "historical conjunction frequency (+ debris-involved %)",
            "orbital lifetime + regime (trade-off axis)",
        ],
        "is": ["data-backed relative comparison of candidate orbits",
               "type-aware (debris-heavy band flagged even at low count)",
               "empirical conjunction history (96% NORAD->altitude matched)"],
        "is_not": ["collision-probability prediction for a specific spacecraft",
                   "fragmentation/breakup modelling (needs NASA SBM)",
                   "resolution/mass payload trade (mission engineering)",
                   "a single collapsed risk score"],
        "interpretation": "Congestion/exposure proxy for orbit selection, the "
                          "design-phase analogue of Launch Screening. Inspired "
                          "by NASA's Feb-2026 design-phase collision-risk tooling.",
    }
