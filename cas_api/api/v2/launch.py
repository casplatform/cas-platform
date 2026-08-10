"""Launch Window Conjunction Screening endpoint — /api/v2/launch/*

Sprint #9. Screen a planned deployment orbit vs the catalog (STEP A), and
optionally sweep RAAN to find a cleaner deployment phase (STEP B).

Positioning: DECISION SUPPORT. CAS screens a user-provided target orbit and
shows close approaches / cleaner phases; the operator decides. CAS does NOT
perform launch-vehicle ascent COLA (launch range / 18 SDS authority), and does
not require deployment data it does not have.

Auth: OPERATOR (JWT required) — operator-facing planning decision support.

IS/IS-NOT honesty: pc_screen is an assumed-sigma SCREENING value (public catalog
carries no covariance); the primary axis is MISS DISTANCE. The RAAN sweep is a
FIRST-ORDER PROXY for launch-window timing (Earth ~15 deg/hr); full epoch-based
COLA (SGP4 propagation of every catalog object to each candidate epoch) and
launch-vehicle ascent COLA are out of scope (Phase 2 / operator ephemeris).
"""
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, model_validator

from core.auth import get_current_user, CurrentUser, require_feature
from services import launch_screen


router = APIRouter(prefix="/launch", tags=["launch"])

# Bound work so a single synchronous request can't monopolise a worker.
# STEP B is raan_steps x full-catalog screens, so it is the costly path.
MAX_SWEEP_STEPS = 12
SWEEP_CATALOG_LIMIT = 80     # tighter band for the sweep (validated ~16-18s @ 8 steps)
SCREEN_CATALOG_LIMIT = 200   # STEP A is a single screen, can afford more


class OrbitInput(BaseModel):
    """Target deployment orbit — provide EITHER orbital elements OR a TLE."""
    model_config = {"extra": "forbid"}

    # Elements path
    altitude_km: Optional[float] = Field(None, ge=130, le=2000,
                                         description="Mean altitude (km); or use a_km")
    a_km: Optional[float] = Field(None, gt=6500, le=9000,
                                  description="Semi-major axis (km); alt to altitude_km")
    ecc: float = Field(0.0, ge=0.0, lt=1.0, description="Eccentricity")
    inc_deg: Optional[float] = Field(None, ge=0.0, le=180.0, description="Inclination (deg)")
    raan_deg: float = Field(0.0, ge=0.0, lt=360.0, description="RAAN (deg)")
    aop_deg: float = Field(0.0, ge=0.0, lt=360.0, description="Argument of perigee (deg)")
    true_anomaly_deg: float = Field(0.0, ge=0.0, lt=360.0, description="True anomaly (deg)")
    # TLE path
    tle1: Optional[str] = Field(None, description="TLE line 1")
    tle2: Optional[str] = Field(None, description="TLE line 2")
    name: Optional[str] = Field("TARGET", max_length=64)

    @model_validator(mode="after")
    def _need_orbit(self):
        has_tle = bool(self.tle1 and self.tle2)
        has_elem = (self.altitude_km is not None or self.a_km is not None) and self.inc_deg is not None
        if not has_tle and not has_elem:
            raise ValueError("provide a TLE (tle1+tle2) OR elements "
                             "(altitude_km or a_km, plus inc_deg)")
        return self

    def to_orbit_dict(self) -> Dict[str, Any]:
        if self.tle1 and self.tle2:
            return {"tle1": self.tle1, "tle2": self.tle2, "name": self.name or "TARGET"}
        return {
            "altitude_km": self.altitude_km, "a_km": self.a_km,
            "ecc": self.ecc, "inc_deg": self.inc_deg, "raan_deg": self.raan_deg,
            "aop_deg": self.aop_deg, "true_anomaly_deg": self.true_anomaly_deg,
            "name": self.name or "TARGET",
        }


class LaunchScreenRequest(BaseModel):
    model_config = {"extra": "forbid"}

    orbit: OrbitInput
    hours: int = Field(48, ge=6, le=72, description="Screening window (hours)")
    threshold_km: float = Field(25.0, gt=0, le=100, description="Close-approach report threshold (km)")
    sigma_m: float = Field(100.0, gt=0, le=10000, description="Assumed screening sigma (m)")
    hbr_m: float = Field(10.0, gt=0, le=1000, description="Hard-body radius (m)")
    sweep: bool = Field(False, description="Also run RAAN-phase sweep (STEP B)")
    sweep_steps: int = Field(8, ge=4, le=MAX_SWEEP_STEPS, description="RAAN sweep steps")


@router.post("/screen")
async def screen(
    req: LaunchScreenRequest,
    user: CurrentUser = Depends(require_feature("mission_design_access")),
) -> Dict[str, Any]:
    """Screen a target deployment orbit vs the catalog (STEP A), optionally with
    a RAAN-phase sweep (STEP B) to surface a cleaner deployment phase."""
    orbit = req.orbit.to_orbit_dict()
    try:
        result_a = launch_screen.screen_orbit(
            orbit, hours=req.hours, threshold_km=req.threshold_km,
            catalog_limit=SCREEN_CATALOG_LIMIT,
            sigma_m=req.sigma_m, hbr_m=req.hbr_m,
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                            detail=f"screen failed: {e}")

    out: Dict[str, Any] = {"static_screen": result_a, "raan_sweep": None}

    if req.sweep:
        steps = max(4, min(int(req.sweep_steps), MAX_SWEEP_STEPS))
        try:
            out["raan_sweep"] = launch_screen.sweep_raan(
                orbit, raan_steps=steps, hours=req.hours,
                threshold_km=req.threshold_km,
                catalog_limit=SWEEP_CATALOG_LIMIT,
                sigma_m=req.sigma_m, hbr_m=req.hbr_m,
            )
        except ValueError as e:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
        except Exception as e:
            # Sweep failure should not void a valid static screen.
            out["raan_sweep"] = {"error": f"sweep failed: {e}"}

    return out


@router.get("/info")
async def info(user: CurrentUser = Depends(get_current_user)) -> Dict[str, Any]:
    """Describe the launch-screening capability and its honest boundaries."""
    return {
        "capability": "Launch Window Conjunction Screening",
        "steps": {
            "A_static_screen": "Screen a user-provided target orbit vs the "
                               "altitude-banded catalog; returns close approaches "
                               "(coarse candidate pass + fine refinement) ranked by miss.",
            "B_raan_sweep": "Sweep RAAN over [0,360) and screen at each phase; "
                            "first-order proxy for launch-window timing "
                            "(Earth ~15 deg/hr). Returns the safest MEASURED phase "
                            "(largest minimum miss) plus phases with no measured approaches.",
        },
        "input": "Orbital elements (altitude_km or a_km + inc_deg, optional ecc/raan/aop/nu) "
                 "OR a TLE (tle1+tle2).",
        "is": ["close-approach screening of a user orbit vs catalog",
               "RAAN-phase sweep as a launch-window timing proxy",
               "miss-distance-primary decision support"],
        "is_not": ["launch-vehicle ascent COLA (launch range / 18 SDS)",
                   "full epoch-based COLA with per-epoch SGP4 catalog propagation (Phase 2)",
                   "operator-covariance Pc (public catalog carries none — assumed-sigma screening)"],
        "auth": "operator (JWT)",
    }
