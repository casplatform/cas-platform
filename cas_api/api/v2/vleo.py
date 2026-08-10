"""VLEO drag-aware decision-support endpoints — /api/v2/vleo/*

Phase 2. Exposes the standalone vleo.py physics layer (NRLMSIS 2.1 density,
drag make-up ΔV forecast, urgency triage) over the FastAPI v2 surface.

  GET  /api/v2/vleo/status     PUBLIC  — readiness + active atmosphere model
  POST /api/v2/vleo/assess     OPERATOR — full conjunction assessment
  POST /api/v2/vleo/delta-v    OPERATOR — drag make-up ΔV forecast only

Positioning: DECISION SUPPORT. CAS quantifies drag-driven uncertainty, ΔV
budget and triage priority; the operator decides and executes. No autonomous
execution, no command generation. Human-in-the-loop is preserved.

IS/IS-NOT honesty: the urgency score is a triage SIGNAL, not a Pc replacement
or a maneuver trigger. The deterministic Pc and operator judgement remain
authoritative. Density is model-tagged (NRLMSIS 2.1 vs USSA76 fallback).
"""
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from core.auth import get_current_user, CurrentUser, require_feature
from services import vleo_service


router = APIRouter(prefix="/vleo", tags=["vleo"])


# ── Request models (server-side clamps via Field bounds) ──────────────────
class AssessRequest(BaseModel):
    """One VLEO conjunction. Solar/geomagnetic drivers optional (defaults are
    moderate activity). ap_index, if given, overrides kp_index for MSIS."""
    model_config = {"extra": "forbid"}

    altitude_km: float = Field(..., gt=100, le=2000, description="Object altitude (km)")
    miss_distance_m: float = Field(..., gt=0, le=1e6, description="Predicted miss distance (m)")
    pc_standard: float = Field(..., ge=0, le=1, description="Engine-computed standard Pc")
    time_to_tca_hours: float = Field(24.0, ge=0, le=336, description="Hours until TCA")
    ballistic_coef: Optional[float] = Field(None, gt=1, le=1000,
                                            description="B = m/(Cd*A) in kg/m² (default 50)")
    forecast_days: float = Field(30.0, gt=0, le=365, description="ΔV forecast horizon (days)")
    kp_index: float = Field(3.0, ge=0, le=9, description="Geomagnetic Kp index")
    f107_flux: float = Field(150.0, ge=60, le=400, description="F10.7 solar radio flux (SFU)")
    ap_index: Optional[float] = Field(None, ge=0, le=400,
                                      description="Geomagnetic Ap (overrides kp for MSIS)")


class DeltaVRequest(BaseModel):
    """Drag make-up ΔV forecast for one altitude/spacecraft."""
    model_config = {"extra": "forbid"}

    altitude_km: float = Field(..., gt=100, le=2000, description="Altitude (km)")
    ballistic_coef: Optional[float] = Field(None, gt=1, le=1000,
                                            description="B = m/(Cd*A) in kg/m² (default 50)")
    forecast_days: float = Field(30.0, gt=0, le=365, description="Horizon (days)")
    f107_flux: float = Field(150.0, ge=60, le=400, description="F10.7 solar flux (SFU)")
    ap_index: float = Field(15.0, ge=0, le=400, description="Geomagnetic Ap index")


class ManeuverSimRequest(BaseModel):
    """Continuous drag-compensation station-keeping simulation (Phase 3).

    compensation = fraction of drag offset by continuous thrust:
      1.0 = full hold (altitude maintained), 0.5 = partial, 0.0 = ballistic.
    spacecraft_mass_kg is optional; if given, average thrust force (mN) is
    reported alongside the specific ΔV budget."""
    model_config = {"extra": "forbid"}

    altitude_km: float = Field(..., gt=120, le=2000, description="Initial altitude (km)")
    compensation: float = Field(1.0, ge=0.0, le=1.0,
                                description="Drag-offset fraction (1=hold, 0=ballistic)")
    duration_days: float = Field(30.0, gt=0, le=365, description="Simulation horizon (days)")
    ballistic_coef: Optional[float] = Field(None, gt=1, le=1000,
                                            description="B = m/(Cd*A) in kg/m² (default 50)")
    spacecraft_mass_kg: Optional[float] = Field(None, gt=0, le=100000,
                                                description="Mass for thrust-force (mN) report")
    f107_flux: float = Field(150.0, ge=60, le=400, description="F10.7 solar flux (SFU)")
    ap_index: float = Field(15.0, ge=0, le=400, description="Geomagnetic Ap index")


class CascadeRequest(BaseModel):
    """VLEO transient cascade-window assessment (Phase 3).

    Models a fragmentation at `altitude_km` via the NASA Standard Breakup Model
    and drag clearing. target/projectile masses + relative velocity determine
    whether the event is catastrophic (>=40 J/g) and the fragment count."""
    model_config = {"extra": "forbid"}

    altitude_km: float = Field(..., gt=120, le=2000, description="Fragmentation altitude (km)")
    norad: Optional[int] = Field(None, ge=1, le=999999,
                                 description="Catalogue ID; if set and no explicit mass is "
                                             "given, the published mass is used")
    target_mass_kg: float = Field(260.0, gt=0, le=100000, description="Larger object mass (kg)")
    projectile_mass_kg: float = Field(1.0, gt=0, le=100000, description="Smaller object mass (kg)")
    rel_velocity_km_s: float = Field(10.0, gt=0, le=20, description="Relative impact velocity (km/s)")
    f107_flux: float = Field(150.0, ge=60, le=400, description="F10.7 solar flux (SFU)")
    lc_min_m: float = Field(0.1, gt=0.001, le=1.0, description="Min fragment size counted (m)")


class FuelTradespaceRequest(BaseModel):
    """VLEO combined drag-makeup + collision-avoidance fuel trade-space (Phase 3).

    Builds a CA-ΔV-vs-lead-time trade-space for a target added separation, plus
    the saving from aligning the CA burn with the prograde drag make-up already
    spent. Illustrative geometry — a specific conjunction uses its real miss
    vector, TCA and relative velocity."""
    model_config = {"extra": "forbid"}

    altitude_km: float = Field(..., gt=120, le=2000, description="Object altitude (km)")
    target_separation_m: float = Field(1000.0, gt=0, le=100000, description="Desired added separation at TCA (m)")
    ballistic_coef: Optional[float] = Field(None, gt=1, le=1000, description="B = m/(Cd*A) (kg/m², default 50)")
    f107_flux: float = Field(150.0, ge=60, le=400, description="F10.7 solar flux (SFU)")
    ap_index: float = Field(15.0, ge=0, le=400, description="Geomagnetic Ap index")
    forecast_days: float = Field(30.0, gt=0, le=365, description="Drag-makeup budget horizon (days)")


# ── Endpoints ─────────────────────────────────────────────────────────────
@router.get("/status")
async def vleo_status(user: CurrentUser = Depends(get_current_user)) -> Dict[str, Any]:
    """AUTHENTICATED — readiness and which atmosphere model is active."""
    return vleo_service.status()


@router.post("/assess")
async def vleo_assess(req: AssessRequest,
                      user: CurrentUser = Depends(require_feature("vleo_access"))) -> Dict[str, Any]:
    """OPERATOR — full VLEO conjunction assessment (urgency + ΔV + drag uncertainty)."""
    if not vleo_service.is_ready():
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                            detail="VLEO service not loaded")
    try:
        result = vleo_service.assess(
            altitude_km=req.altitude_km, miss_distance_m=req.miss_distance_m,
            pc_standard=req.pc_standard, time_to_tca_hours=req.time_to_tca_hours,
            ballistic_coef=req.ballistic_coef, forecast_days=req.forecast_days,
            kp_index=req.kp_index, f107_flux=req.f107_flux, ap_index=req.ap_index,
        )
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                            detail=f"assess failed: {type(e).__name__}: {e}")
    if isinstance(result, dict) and result.get("error"):
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=result)
    result["requested_by"] = {"user_id": user.id, "role": user.role}
    return result


@router.post("/delta-v")
async def vleo_delta_v(req: DeltaVRequest,
                       user: CurrentUser = Depends(require_feature("vleo_access"))) -> Dict[str, Any]:
    """OPERATOR — drag make-up ΔV forecast (+ unmaintained decay path)."""
    if not vleo_service.is_ready():
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                            detail="VLEO service not loaded")
    try:
        result = vleo_service.delta_v_forecast(
            altitude_km=req.altitude_km, ballistic_coef=req.ballistic_coef,
            forecast_days=req.forecast_days, f107_flux=req.f107_flux,
            ap_index=req.ap_index,
        )
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                            detail=f"delta-v failed: {type(e).__name__}: {e}")
    if isinstance(result, dict) and result.get("error"):
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=result)
    result["requested_by"] = {"user_id": user.id, "role": user.role}
    return result


@router.post("/maneuver-sim")
async def vleo_maneuver_sim(req: ManeuverSimRequest,
                           user: CurrentUser = Depends(require_feature("vleo_access"))) -> Dict[str, Any]:
    """Continuous drag-compensation station-keeping simulation (OPERATOR).

    Simulates holding a VLEO orbit against atmospheric drag with continuous
    low-thrust (GOCE/Starlink-style), returning the altitude profile over the
    horizon and the specific ΔV the thruster spends. The operator chooses the
    compensation policy; this quantifies its cost.

    IS: a station-keeping propellant/thrust simulation (decision support).
    IS NOT: thruster design, an autonomous control loop, or a propellant
    guarantee. The operator decides and executes.
    """
    if not vleo_service.is_ready():
        raise HTTPException(status_code=503, detail="VLEO module not available")
    result = vleo_service.maneuver_sim(
        altitude_km=req.altitude_km, compensation=req.compensation,
        duration_days=req.duration_days, ballistic_coef=req.ballistic_coef,
        spacecraft_mass_kg=req.spacecraft_mass_kg,
        f107_flux=req.f107_flux, ap_index=req.ap_index,
    )
    if isinstance(result, dict) and result.get("error"):
        raise HTTPException(status_code=503, detail=result)
    result["requested_by"] = {"user_id": user.id, "role": user.role}
    return result


@router.post("/cascade")
async def vleo_cascade(req: CascadeRequest,
                      user: CurrentUser = Depends(require_feature("vleo_access"))) -> Dict[str, Any]:
    """VLEO transient cascade-window assessment (OPERATOR).

    Estimates the drag-limited self-cleaning timeline after a VLEO
    fragmentation: NASA SBM fragment count, per-size clearing times, the
    transient elevated-risk window, and the downward sweep into lower bands.

    IS: a drag-limited transient cascade-window estimate (self-cleaning).
    IS NOT: a full NASA EVOLVE/LEGEND breakup simulation, per-fragment orbit
    propagation, or a claim of persistent Kessler instability in VLEO.
    """
    if not vleo_service.is_ready():
        raise HTTPException(status_code=503, detail="VLEO module not available")
    # Fragment production scales with colliding mass, so the 260 kg default is
    # wrong by orders of magnitude for most real objects. If a catalogue ID is
    # given and no explicit mass was set, use the published figure.
    mass = req.target_mass_kg
    mass_src = "user-provided" if req.target_mass_kg != 260.0 else "default"
    if req.norad and req.target_mass_kg == 260.0:
        try:
            from core.database import get_dict_cursor
            with get_dict_cursor() as _c:
                _c.execute("SELECT mass_kg, mass_source FROM satcat_objects "
                           "WHERE norad=%s AND mass_kg IS NOT NULL", (req.norad,))
                _r = _c.fetchone()
                if _r and _r.get("mass_kg"):
                    mass = float(_r["mass_kg"])
                    mass_src = _r.get("mass_source") or "catalogue"
        except Exception:
            pass

    result = vleo_service.cascade(
        altitude_km=req.altitude_km, target_mass_kg=mass,
        projectile_mass_kg=req.projectile_mass_kg,
        rel_velocity_km_s=req.rel_velocity_km_s,
        f107_flux=req.f107_flux, lc_min_m=req.lc_min_m,
    )
    if isinstance(result, dict) and result.get("error"):
        raise HTTPException(status_code=503, detail=result)
    result["mass_used_kg"] = mass
    result["mass_source"] = mass_src
    result["requested_by"] = {"user_id": user.id, "role": user.role}
    return result


@router.post("/fuel-tradespace")
async def vleo_fuel_tradespace_ep(req: FuelTradespaceRequest,
                                 user: CurrentUser = Depends(require_feature("vleo_access"))) -> Dict[str, Any]:
    """VLEO combined drag-makeup + collision-avoidance fuel trade-space (OPERATOR).

    Returns CA ΔV at several maneuver lead times for a target added separation,
    the drag make-up budget context, and the saving from reusing the prograde-
    aligned fraction of the CA burn. Options + costs for the operator to weigh.

    IS: a fuel/timing trade-space for the operator (options + costs).
    IS NOT: an autonomous maneuver command, a recommendation, a propellant
    guarantee, or maneuver execution. The operator decides.
    """
    if not vleo_service.is_ready():
        raise HTTPException(status_code=503, detail="VLEO module not available")
    result = vleo_service.fuel_tradespace(
        altitude_km=req.altitude_km, target_separation_m=req.target_separation_m,
        ballistic_coef=req.ballistic_coef, f107_flux=req.f107_flux,
        ap_index=req.ap_index, forecast_days=req.forecast_days,
    )
    if isinstance(result, dict) and result.get("error"):
        raise HTTPException(status_code=503, detail=result)
    result["requested_by"] = {"user_id": user.id, "role": user.role}
    return result
