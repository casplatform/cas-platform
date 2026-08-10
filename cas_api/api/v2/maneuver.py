"""Maneuver Trade Space endpoint — /api/v2/maneuver/*

Sprint #7. NASA CARA MTS-style (lead_time x delta-v) sweep for one conjunction.

Positioning: DECISION SUPPORT. CAS evaluates maneuver options; the operator
decides and executes. No autonomous execution, no command generation.

Auth: OPERATOR (JWT) + Pro plan or higher (tier feature: maneuver_access).
Maneuver evaluation is operator-facing decision support.

IS/IS-NOT honesty: public Space-Track CDMs carry no covariance, so pc_screen is
an assumed-sigma SCREENING value; the primary axis is MISS DISTANCE. A full
post-maneuver Pc trade space unlocks with operator covariance (G1 gate).
"""
from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, model_validator

from core.auth import get_current_user, CurrentUser, require_feature
from core.config import settings
from services import maneuver_sim


router = APIRouter(prefix="/maneuver", tags=["maneuver"])

# Bound the grid so a single synchronous request can't monopolise a worker.
MAX_LEADS = 10
MAX_DVS = 9
MAX_CELLS = 140


class TradeSpaceRequest(BaseModel):
    """Identify ONE conjunction by event_id, cdm_id, or NORAD pair.
    Optional grid overrides are clamped server-side."""
    model_config = {"extra": "forbid"}

    event_id: Optional[int] = Field(None, description="conjunction_events.id")
    cdm_id: Optional[str] = Field(None, description="CDM_ID (latest row used)")
    norad1: Optional[str] = Field(None, description="NORAD of object 1")
    norad2: Optional[str] = Field(None, description="NORAD of object 2")
    primary: str = Field("sat1", pattern="^(sat1|sat2)$",
                         description="Which object maneuvers (sat1=object1)")
    leads_h: Optional[List[float]] = Field(None, description="Lead times (hours)")
    dvs_ms: Optional[List[float]] = Field(None, description="Delta-v magnitudes (m/s)")
    directions: Optional[List[str]] = Field(None, description="prograde/retrograde/radial_out")
    sigma_m: float = Field(100.0, gt=0, le=10000, description="Assumed screening sigma (m)")
    hbr_m: float = Field(10.0, gt=0, le=1000, description="Hard-body radius (m)")
    do_cascade: bool = Field(True, description="Re-screen recommended option vs catalog")
    cascade_hours: int = Field(36, ge=6, le=72)

    @model_validator(mode="after")
    def _need_identifier(self):
        if self.event_id is None and not self.cdm_id and not (self.norad1 and self.norad2):
            raise ValueError("provide event_id, cdm_id, or both norad1 and norad2")
        return self


def _clamp(req: TradeSpaceRequest) -> Dict[str, Any]:
    leads = req.leads_h
    dvs = req.dvs_ms
    dirs = req.directions
    if leads is not None:
        leads = sorted({float(x) for x in leads if x > 0})[:MAX_LEADS] or None
    if dvs is not None:
        dvs = sorted({float(x) for x in dvs if x > 0})[:MAX_DVS] or None
    if dirs is not None:
        allowed = {"prograde", "retrograde", "radial_out"}
        dirs = [d for d in dirs if d in allowed] or None
        if dirs is not None:
            dirs = tuple(dirs)
    return {"leads_h": leads, "dvs_ms": dvs, "directions": dirs}


@router.post("/trade-space")
async def trade_space(
    req: TradeSpaceRequest,
    user: CurrentUser = Depends(require_feature("maneuver_access")),
) -> Dict[str, Any]:
    """Compute the maneuver trade space for one conjunction.

    Returns: no-burn baseline (SGP4 truth) + grid(lead x dv -> miss/pc_screen) +
    recommended (min-dv clearing the screening threshold at shortest feasible
    lead) + cascade re-screen of the recommended option + IS/IS-NOT note.
    """
    pair: Optional[Tuple[str, str]] = (
        (req.norad1, req.norad2) if (req.norad1 and req.norad2) else None
    )

    # Load the event (DB + TLE resolution). 404 if not found / no resolvable TLEs.
    try:
        event = maneuver_sim.load_event(
            dsn=settings.db_url or None,
            event_id=req.event_id,
            cdm_id=req.cdm_id,
            norad_pair=pair,
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"event not resolvable: {type(e).__name__}: {e}",
        )

    overrides = _clamp(req)
    kwargs: Dict[str, Any] = {
        "primary": req.primary,
        "sigma_m": req.sigma_m,
        "hbr_m": req.hbr_m,
        "do_cascade": req.do_cascade,
        "cascade_hours": req.cascade_hours,
    }
    if overrides["leads_h"] is not None:
        kwargs["leads_h"] = overrides["leads_h"]
    if overrides["dvs_ms"] is not None:
        kwargs["dvs_ms"] = overrides["dvs_ms"]
    if overrides["directions"] is not None:
        kwargs["directions"] = overrides["directions"]

    # Cell-count guard (cap compute on a synchronous worker).
    n_leads = len(kwargs.get("leads_h") or maneuver_sim.DEFAULT_LEADS_H)
    n_dvs = len(kwargs.get("dvs_ms") or maneuver_sim.DEFAULT_DVS_MS)
    n_dirs = len(kwargs.get("directions") or maneuver_sim.DEFAULT_DIRECTIONS)
    if n_leads * n_dvs * n_dirs > MAX_CELLS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"grid too large: {n_leads}x{n_dvs}x{n_dirs} > {MAX_CELLS} cells",
        )

    try:
        result = maneuver_sim.build_trade_space(event, **kwargs)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"trade-space failed: {type(e).__name__}: {e}",
        )

    if isinstance(result, dict) and result.get("error"):
        # Domain-level "no actionable maneuver" (e.g. TCA too close) -> 422.
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                            detail=result)

    result["requested_by"] = {"user_id": user.id, "role": user.role}
    return result


@router.get("/info")
async def maneuver_info(user: CurrentUser = Depends(get_current_user)) -> Dict[str, Any]:
    """Static capability/positioning descriptor (auth-gated)."""
    return {
        "kind": "maneuver_trade_space_info",
        "positioning": ("Decision support — CAS evaluates maneuver options; the "
                        "operator decides and executes. No autonomous execution."),
        "model": {
            "pre_burn": "SGP4 (TEME)",
            "post_burn": "RK4 two-body + J2 (engine parity)",
            "baseline": "SGP4 truth, lead-independent",
            "pc": "assumed-sigma screening (covariance G1-gated)",
        },
        "defaults": {
            "leads_h": maneuver_sim.DEFAULT_LEADS_H,
            "dvs_ms": maneuver_sim.DEFAULT_DVS_MS,
            "directions": list(maneuver_sim.DEFAULT_DIRECTIONS),
        },
        "limits": {"max_leads": MAX_LEADS, "max_dvs": MAX_DVS, "max_cells": MAX_CELLS},
        "engine_parity": maneuver_sim.ENGINE_PARITY,
    }
