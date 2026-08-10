"""Insurance endpoints — /api/v2/insurance/*

Orbital exposure characterisation for space insurance underwriters. Separate
product line from the operator platform: role-gated so that operators never see
insurance modules and insurers never see operator modules.

DECISION SUPPORT — CAS reports the orbital exposure of a shell. Premium,
coverage structure and risk appetite remain entirely with the underwriter.
"""
import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field

from core.auth import get_current_user, CurrentUser, require_feature
from services import orbital_risk as orisk
from services import insurance_archive as archive
from services import insurance_demo as demo_scenarios
from services import insurance_watch as watch_svc
from services import ocbi as ocbi_svc
from services.insurance_report import (render_insurance_pdf,
                                       render_insurance_xlsx)

log = logging.getLogger(__name__)
router = APIRouter(tags=["insurance"])


# ─────────────────────────── role gate ───────────────────────────
def require_insurer(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
    """Insurance endpoints require role='insurer' (or admin), THEN a tier.

    Role is checked FIRST: an operator must not be told to "upgrade" — the
    insurance modules are a separate product line, not a higher operator tier.
    """
    from core.tier_features import has_feature
    if user.role not in ("insurer", "admin"):
        raise HTTPException(
            status_code=403,
            detail={
                "error": "role_required",
                "required_role": "insurer",
                "current_role": user.role,
                "message": ("Insurance modules are a separate product line, "
                            "not an operator tier. Contact us to enable "
                            "insurer access."),
            },
        )
    if user.role != "admin" and not has_feature(user.tier, "insurance_access"):
        raise HTTPException(
            status_code=403,
            detail={
                "error": "insurance_tier_required",
                "current_tier": user.tier,
                "message": "Your insurance plan does not include this feature.",
            },
        )
    return user


def _is_demo(user: CurrentUser) -> bool:
    return str(user.tier).lower() == "insurer_demo"


# ─────────────────────────── schemas ───────────────────────────
class OrbitAssessRequest(BaseModel):
    scenario: Optional[str] = Field(default=None, max_length=24,
                                    description="Demo scenario id (demo accounts only)")
    norad: Optional[int] = Field(default=None, ge=1, le=999999,
                                 description="Catalogue ID; if given, its orbit "
                                             "is resolved and altitude/inclination ignored")
    altitude_km: float = Field(default=550.0, ge=150.0, le=2000.0,
                               description="Circular orbit altitude (km), LEO range")
    inclination_deg: Optional[float] = Field(
        default=None, ge=0.0, le=180.0,
        description="Inclination (deg). If given, exposure is gated to "
                    "crossing-relevant planes; if omitted, whole altitude shell.")
    band_half_km: float = Field(default=25.0, ge=5.0, le=100.0)
    inclination_tolerance_deg: float = Field(default=5.0, ge=1.0, le=20.0)
    trend_mode: str = Field(default="inclination",
                            description="'inclination' (gated) or 'band' (all inclinations)")
    label: Optional[str] = Field(default=None, max_length=80)


class PortfolioAsset(BaseModel):
    label: str = Field(..., max_length=80)
    altitude_km: float = Field(..., ge=150.0, le=2000.0)
    inclination_deg: Optional[float] = Field(default=None, ge=0.0, le=180.0)


class PortfolioRequest(BaseModel):
    assets: List[PortfolioAsset] = Field(..., min_length=1, max_length=200)
    band_half_km: float = Field(default=25.0, ge=5.0, le=100.0)
    trend_mode: str = Field(default="inclination")


# ─────────────────────────── endpoints ───────────────────────────
@router.post("/insurance/assess")
async def assess(req: OrbitAssessRequest,
                 user: CurrentUser = Depends(require_insurer)) -> Dict[str, Any]:
    """Orbital Risk Factor Sheet for a single orbit."""
    is_demo_acct = _is_demo(user)
    if is_demo_acct and not req.scenario:
        # Demo accounts never touch live data — synthetic scenarios only.
        raise HTTPException(
            status_code=400,
            detail={"error": "scenario_required",
                    "message": "Demo accounts run on synthetic scenarios. "
                               "Provide a scenario id.",
                    "available": [x["id"] for x in demo_scenarios.list_scenarios()]})
    try:
        if is_demo_acct:
            result = demo_scenarios.assess(req.scenario)
        elif req.norad:
            result = orisk.assess_norad(
                norad=req.norad,
                band_half_km=req.band_half_km,
                inc_tol_deg=req.inclination_tolerance_deg,
                trend_mode=req.trend_mode,
            )
        else:
            result = orisk.assess_orbit(
                altitude_km=req.altitude_km,
                inclination_deg=req.inclination_deg,
                band_half_km=req.band_half_km,
                inc_tol_deg=req.inclination_tolerance_deg,
                trend_mode=req.trend_mode,
            )
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        log.exception("insurance assess failed")
        raise HTTPException(status_code=500, detail=f"assessment failed: {e}")

    result["label"] = req.label
    result["demo"] = _is_demo(user)
    result["report_id"] = archive.save(user.id, result, is_demo=_is_demo(user))
    if _is_demo(user):
        result["demo_notice"] = ("DEMO ACCOUNT — synthetic scenario. Not for "
                                 "underwriting use.")
    return result


@router.post("/insurance/portfolio")
async def portfolio(req: PortfolioRequest,
                    user: CurrentUser = Depends(require_insurer)) -> Dict[str, Any]:
    """Portfolio exposure + accumulation concentration (INSURER PRO and above)."""
    from core.tier_features import has_feature
    if user.role != "admin" and not has_feature(user.tier, "portfolio_access"):
        raise HTTPException(
            status_code=403,
            detail={"error": "insurance_tier_required",
                    "current_tier": user.tier,
                    "message": "Portfolio analysis requires INSURER PRO or above."})
    rows: List[Dict[str, Any]] = []
    for a in req.assets:
        try:
            r = orisk.assess_orbit(a.altitude_km, a.inclination_deg,
                                   req.band_half_km, 5.0, req.trend_mode)
            rows.append({
                "label": a.label,
                "altitude_km": a.altitude_km,
                "inclination_deg": a.inclination_deg,
                "threat_objects": r["catalogue"]["threat"],
                "lambda_per_year": r["burden"]["lambda_per_year"],
                "trend_pct_per_year": (r["trend"]["primary"] or {}).get("cagr_pct_per_year"),
            })
        except Exception as e:
            log.warning("portfolio asset %s failed: %s", a.label, e)
            rows.append({"label": a.label, "error": str(e)})

    # accumulation: a fragmentation cloud spreads over roughly +/-100 km, so
    # fixed 100 km buckets under-count real exposure (780 km and 800 km sit in
    # different buckets yet share the same cloud). We therefore slide a
    # +/-100 km window over each asset and take the worst-case overlap.
    ok = [r for r in rows if "error" not in r]
    CLOUD_HALF_KM = 100.0
    worst_center, worst_group = None, []
    for anchor in ok:
        group = [r for r in ok
                 if abs(r["altitude_km"] - anchor["altitude_km"]) <= CLOUD_HALF_KM]
        if len(group) > len(worst_group):
            worst_center, worst_group = anchor["altitude_km"], group
    conc = (len(worst_group) / len(ok)) if ok else 0.0

    # informational: fixed-bucket distribution (easier to eyeball)
    bands: Dict[str, int] = {}
    for r in ok:
        key = f"{int(r['altitude_km'] // 100) * 100}-{int(r['altitude_km'] // 100) * 100 + 100}km"
        bands[key] = bands.get(key, 0) + 1
    top_band = (f"{worst_center - CLOUD_HALF_KM:.0f}-{worst_center + CLOUD_HALF_KM:.0f}km"
                if worst_center is not None else "")
    top_n = len(worst_group)

    return {
        "assets": rows,
        "count": len(rows),
        "accumulation": {
            "band_distribution": bands,
            "worst_case_window": top_band,
            "worst_case_assets": top_n,
            "worst_case_labels": [r["label"] for r in worst_group],
            "concentration_index": round(conc, 2),
            "window_half_km": CLOUD_HALF_KM,
            "note": ("Worst-case overlap: a single catastrophic fragmentation "
                     f"within +/-{CLOUD_HALF_KM:.0f} km of {top_band} would place "
                     f"{top_n} of {len(ok)} assets under elevated risk "
                     "simultaneously. Debris clouds disperse across altitude, "
                     "so fixed bucket boundaries understate real accumulation."),
        },
        "demo": _is_demo(user),
    }


class ReportRequest(OrbitAssessRequest):
    format: str = Field(default="pdf", description="'pdf' or 'xlsx'")
    organisation: Optional[str] = Field(default=None, max_length=120,
                                        description="Name printed on the sheet")
    report_id: Optional[str] = Field(default=None, max_length=12,
                                     description="Reuse an existing report id")


@router.post("/insurance/report")
async def report(req: ReportRequest,
                 user: CurrentUser = Depends(require_insurer)):
    """Orbital Risk Factor Sheet as a downloadable document (PDF or XLSX)."""
    fmt = (req.format or "pdf").lower()
    if fmt not in ("pdf", "xlsx"):
        raise HTTPException(status_code=400, detail="format must be 'pdf' or 'xlsx'")
    rid = req.report_id
    org = req.organisation
    demo = _is_demo(user)
    a = None
    if rid:
        rec = archive.get(user.id, rid)
        if not rec:
            raise HTTPException(status_code=404, detail=f"report {rid} not in your archive")
        a = rec["assessment"]; org = org or rec.get("org")
        demo = rec.get("is_demo", demo); rid = rec["report_id"]
    if a is None and demo and not req.scenario:
        raise HTTPException(
            status_code=400,
            detail={"error": "scenario_required",
                    "message": "Demo accounts generate reports from synthetic "
                               "scenarios only.",
                    "available": [x["id"] for x in demo_scenarios.list_scenarios()]})
    if a is None:
        try:
            if demo:
                a = demo_scenarios.assess(req.scenario)
            elif req.norad:
                a = orisk.assess_norad(
                    norad=req.norad,
                    band_half_km=req.band_half_km,
                    inc_tol_deg=req.inclination_tolerance_deg,
                    trend_mode=req.trend_mode,
                )
            else:
                a = orisk.assess_orbit(
                    altitude_km=req.altitude_km,
                    inclination_deg=req.inclination_deg,
                    band_half_km=req.band_half_km,
                    inc_tol_deg=req.inclination_tolerance_deg,
                    trend_mode=req.trend_mode,
                )
        except ValueError as e:
            raise HTTPException(status_code=404, detail=str(e))
        except Exception as e:
            log.exception("insurance report assess failed")
            raise HTTPException(status_code=500, detail=f"assessment failed: {e}")
        rid = archive.save(user.id, a, org=org, is_demo=demo)
    inc = req.inclination_deg
    tag = f"{int(req.altitude_km)}km" + (f"_{inc:.0f}deg" if inc is not None else "_allinc")
    try:
        if fmt == "pdf":
            content = render_insurance_pdf(a, org=org, demo=demo, report_id=rid)
            media = "application/pdf"
            fname = f"CAS_Orbital_Risk_{tag}.pdf"
        else:
            content = render_insurance_xlsx(a, org=org, demo=demo, report_id=rid)
            media = ("application/vnd.openxmlformats-officedocument"
                     ".spreadsheetml.sheet")
            fname = f"CAS_Orbital_Risk_{tag}.xlsx"
    except Exception as e:
        log.exception("insurance report render failed")
        raise HTTPException(status_code=500, detail=f"render failed: {e}")

    return Response(content=content, media_type=media,
                    headers={"Content-Disposition": f'attachment; filename="{fname}"'})


@router.get("/insurance/reports")
async def list_reports(q: Optional[str] = None,
                       date_from: Optional[str] = None,
                       date_to: Optional[str] = None,
                       limit: int = 100,
                       user: CurrentUser = Depends(require_insurer)) -> Dict[str, Any]:
    """Archived reports; q matches the report id."""
    rows = archive.search(user.id, q=q, date_from=date_from, date_to=date_to, limit=limit)
    return {"reports": rows, "count": len(rows), "total_archived": archive.count(user.id)}


@router.get("/insurance/reports/{report_id}")
async def get_report(report_id: str,
                     user: CurrentUser = Depends(require_insurer)) -> Dict[str, Any]:
    """Frozen assessment, exactly as issued."""
    rec = archive.get(user.id, report_id)
    if not rec:
        raise HTTPException(status_code=404, detail="report not found")
    return rec


@router.get("/insurance/scenarios")
async def scenarios(user: CurrentUser = Depends(require_insurer)) -> Dict[str, Any]:
    """Synthetic demo scenarios (available to every insurer account)."""
    return {"scenarios": demo_scenarios.list_scenarios(),
            "demo_account": _is_demo(user),
            "note": ("Synthetic scenarios use the same physics and formula as the "
                     "production system, but the objects are fictional.")}


class WatchRequest(BaseModel):
    label: str = Field(..., max_length=120)
    altitude_km: float = Field(..., ge=150.0, le=2000.0)
    inclination_deg: Optional[float] = Field(default=None, ge=0.0, le=180.0)
    band_half_km: float = Field(default=25.0, ge=5.0, le=100.0)
    watch_threat: bool = True
    threat_pct: float = Field(default=10.0, ge=1.0, le=100.0)
    watch_pctl: bool = True
    pctl_points: float = Field(default=5.0, ge=1.0, le=50.0)
    watch_frag: bool = True


class WatchUpdate(BaseModel):
    label: Optional[str] = Field(default=None, max_length=120)
    threat_pct: Optional[float] = Field(default=None, ge=1.0, le=100.0)
    pctl_points: Optional[float] = Field(default=None, ge=1.0, le=50.0)
    watch_threat: Optional[bool] = None
    watch_pctl: Optional[bool] = None
    watch_frag: Optional[bool] = None
    is_active: Optional[bool] = None


def _watch_limit(user: CurrentUser) -> int:
    from core.tier_features import _TIER_FEATURES
    if user.role == "admin":
        return -1
    return int((_TIER_FEATURES.get(str(user.tier), {}) or {}).get("watch_limit", 0))


@router.post("/insurance/watch")
async def watch_create(req: WatchRequest,
                       user: CurrentUser = Depends(require_insurer)) -> Dict[str, Any]:
    """Monitor an insured orbit and alert when it materially deteriorates."""
    limit = _watch_limit(user)
    if limit == 0:
        raise HTTPException(status_code=403, detail={
            "error": "insurance_tier_required", "current_tier": user.tier,
            "message": "Threshold monitoring requires INSURER PRO or above."})
    if limit > 0 and watch_svc.count_active(user.id) >= limit:
        raise HTTPException(status_code=403, detail={
            "error": "watch_limit_reached", "limit": limit,
            "message": f"Your plan allows {limit} monitored orbits. "
                       "Deactivate one or upgrade to Enterprise."})
    try:
        return watch_svc.create(
            user.id, label=req.label, altitude_km=req.altitude_km,
            inclination_deg=req.inclination_deg, band_half_km=req.band_half_km,
            watch_threat=req.watch_threat, threat_pct=req.threat_pct,
            watch_pctl=req.watch_pctl, pctl_points=req.pctl_points,
            watch_frag=req.watch_frag)
    except Exception as e:
        log.exception("watch create failed")
        raise HTTPException(status_code=500, detail=f"could not create watch: {e}")


@router.get("/insurance/watch")
async def watch_list(include_inactive: bool = False,
                     user: CurrentUser = Depends(require_insurer)) -> Dict[str, Any]:
    limit = _watch_limit(user)
    return {"watches": watch_svc.listing(user.id, include_inactive),
            "active": watch_svc.count_active(user.id),
            "limit": ("unlimited" if limit < 0 else limit),
            "recent_events": watch_svc.events(user.id, 20)}


@router.patch("/insurance/watch/{watch_id}")
async def watch_update(watch_id: int, req: WatchUpdate,
                       user: CurrentUser = Depends(require_insurer)) -> Dict[str, Any]:
    ok = watch_svc.update(user.id, watch_id, **req.model_dump(exclude_none=True))
    if not ok:
        raise HTTPException(status_code=404, detail="watch not found or nothing to update")
    return {"updated": True, "id": watch_id}


@router.delete("/insurance/watch/{watch_id}")
async def watch_delete(watch_id: int,
                       user: CurrentUser = Depends(require_insurer)) -> Dict[str, Any]:
    if not watch_svc.remove(user.id, watch_id):
        raise HTTPException(status_code=404, detail="watch not found")
    return {"deleted": True, "id": watch_id}


@router.get("/insurance/watch/events")
async def watch_events(limit: int = 50,
                       user: CurrentUser = Depends(require_insurer)) -> Dict[str, Any]:
    return {"events": watch_svc.events(user.id, limit)}


class OcbiOrbit(BaseModel):
    label: str = Field(..., max_length=64)
    altitude_km: float = Field(..., ge=150, le=2000)
    inclination_deg: Optional[float] = Field(None, ge=0, le=180)


class OcbiRequest(BaseModel):
    orbits: List[OcbiOrbit] = Field(..., min_length=3, max_length=20)


@router.post("/insurance/ocbi")
async def ocbi(req: OcbiRequest,
               user: CurrentUser = Depends(require_insurer)) -> Dict[str, Any]:
    """OCBI — Orbital Collision-Burden Index across a peer set of orbits.

    RELATIVE index: kappa and cascade are normalised across the supplied peer
    set, so at least 3 orbits are required and a score is only meaningful
    against the others in the same request.

    CAS reports collective orbital burden. Insured value, launcher record and
    operator record remain entirely with the underwriter.
    """
    try:
        conj = ocbi_svc.load_conjunction_altitudes()
        out = ocbi_svc.ocbi_batch(
            [(o.label, o.altitude_km, o.inclination_deg) for o in req.orbits],
            conj,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        log.exception("insurance ocbi failed")
        raise HTTPException(status_code=500, detail=f"ocbi failed: {e}")

    out["demo"] = _is_demo(user)
    if _is_demo(user):
        out["demo_notice"] = ("DEMO ACCOUNT — synthetic scenario. Not for "
                              "underwriting use.")
    return out


@router.get("/insurance/methodology")
async def methodology(user: CurrentUser = Depends(get_current_user)) -> Dict[str, Any]:
    """Transparent methodology — open to any authenticated user by design.

    Underwriters need a defensible rationale, not a black box; the methodology
    is therefore not gated behind the insurance tier.
    """
    return orisk.methodology()


@router.get("/insurance/info")
async def info(user: CurrentUser = Depends(get_current_user)) -> Dict[str, Any]:
    """Capability description and honest boundaries."""
    return {
        "capability": "Orbital exposure characterisation for insurance underwriting",
        "produces": ["catalogue state (threat vs active)",
                     "kinetic burden (lambda per year, 10 m^2 reference)",
                     "environment trend (2020-2026, two modes)",
                     "cascade exposure (NASA SBM + drag)",
                     "LEO percentile ranking"],
        "does_not_produce": ["satellite-specific collision probability (no covariance in public catalogues)",
                             "premium or actuarial rate",
                             "lethal non-trackable (1-10 cm) flux — requires ESA MASTER / NASA ORDEM"],
        "role_required": "insurer",
        "your_role": user.role,
        "your_tier": user.tier,
    }
