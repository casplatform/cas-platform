"""VLEO decision-support service — wraps the standalone /opt/cas/vleo.py
physics layer (Phase 1 + Phase 2) for the FastAPI v2 surface.

The heavy physics (NRLMSIS 2.1 density, drag propagation, ΔV forecast, urgency
score) lives in vleo.py and is import-guarded here: if vleo (or its optional
pymsis backend) is unavailable, the service reports not-ready instead of
crashing the API. The engine (port 8765) is never touched — this is the
Strangler-pattern FastAPI layer.

IS:     drag-aware decision support (uncertainty, ΔV budget, triage priority).
IS NOT: an autonomous maneuver decision. Human-in-the-loop is preserved; the
        deterministic Pc and operator judgement remain authoritative.
"""
import sys
from typing import Any, Dict, Optional

# vleo.py lives one directory up from cas_api/.
if "/opt/cas" not in sys.path:
    from core.paths import CAS_HOME as _CH
    sys.path.insert(0, _CH)

_VLEO_ERR: Optional[str] = None
try:
    import vleo as _vleo
    _VLEO_OK = True
except Exception as e:
    _vleo = None
    _VLEO_OK = False
    _VLEO_ERR = f"{type(e).__name__}: {e}"


def is_ready() -> bool:
    return _VLEO_OK


def status() -> Dict[str, Any]:
    """Readiness + which atmosphere model is active."""
    msis = bool(getattr(_vleo, "_MSIS_AVAILABLE", False)) if _VLEO_OK else False
    return {
        "ready": _VLEO_OK,
        "load_error": _VLEO_ERR,
        "density_model": ("NRLMSIS 2.1" if msis else
                          ("USSA76+solar-scaled" if _VLEO_OK else None)),
        "msis_available": msis,
        "regime_thresholds_km": {
            "vleo_below": getattr(_vleo, "REGIME_HYBRID_MIN_KM", None) if _VLEO_OK else None,
            "hybrid_below": getattr(_vleo, "REGIME_VLEO_MAX_KM", None) if _VLEO_OK else None,
        },
        "is": "drag-aware VLEO decision support (Phase 2)",
        "is_not": "an autonomous maneuver decision; human-in-the-loop preserved",
    }


def assess(altitude_km, miss_distance_m, pc_standard,
           time_to_tca_hours=24.0, ballistic_coef=None,
           forecast_days=30.0, kp_index=3.0,
           f107_flux=150.0, ap_index=None) -> Dict[str, Any]:
    """Full VLEO conjunction assessment (urgency + ΔV forecast + drag uncertainty)."""
    if not _VLEO_OK:
        return {"error": "vleo module not loaded", "load_error": _VLEO_ERR}
    bc = ballistic_coef if ballistic_coef is not None else _vleo.DEFAULT_BALLISTIC_COEF
    return _vleo.vleo_conjunction_assessment(
        altitude_km=altitude_km, miss_distance_m=miss_distance_m,
        pc_standard=pc_standard, kp_index=kp_index, f107_flux=f107_flux,
        time_to_tca_hours=time_to_tca_hours, ballistic_coef=bc,
        forecast_days=forecast_days, ap_index=ap_index,
    )


def delta_v_forecast(altitude_km, ballistic_coef=None,
                     forecast_days=30.0, f107_flux=150.0,
                     ap_index=15.0) -> Dict[str, Any]:
    """Drag make-up ΔV forecast over a horizon (+ unmaintained decay path)."""
    if not _VLEO_OK:
        return {"error": "vleo module not loaded", "load_error": _VLEO_ERR}
    bc = ballistic_coef if ballistic_coef is not None else _vleo.DEFAULT_BALLISTIC_COEF
    return _vleo.drag_delta_v_forecast(
        altitude_km=altitude_km, ballistic_coef=bc, forecast_days=forecast_days,
        f107=f107_flux, ap=ap_index,
    )


def urgency(altitude_km, miss_distance_m, pc, time_to_tca_hours,
            f107_flux=150.0, ap_index=15.0) -> Dict[str, Any]:
    """Operational urgency score (0–100) for triage."""
    if not _VLEO_OK:
        return {"error": "vleo module not loaded", "load_error": _VLEO_ERR}
    return _vleo.vleo_urgency_score(
        altitude_km=altitude_km, miss_distance_m=miss_distance_m, pc=pc,
        time_to_tca_hours=time_to_tca_hours, f107=f107_flux, ap=ap_index,
    )


def maneuver_sim(altitude_km, compensation=1.0, duration_days=30.0,
                 ballistic_coef=None, spacecraft_mass_kg=None,
                 f107_flux=150.0, ap_index=15.0) -> Dict[str, Any]:
    """Continuous drag-compensation station-keeping simulation (Phase 3).

    Simulates holding (or partially holding) a VLEO orbit against drag with
    continuous low-thrust, returning the altitude profile and thruster ΔV.
    compensation: 1.0 full hold, 0.5 partial, 0.0 ballistic (no thrust).
    """
    if not _VLEO_OK:
        return {"error": "vleo module not loaded", "load_error": _VLEO_ERR}
    bc = ballistic_coef if ballistic_coef is not None else _vleo.DEFAULT_BALLISTIC_COEF
    return _vleo.simulate_continuous_maneuver(
        altitude_km=altitude_km, ballistic_coef=bc, duration_days=duration_days,
        compensation=compensation, spacecraft_mass_kg=spacecraft_mass_kg,
        f107_flux=f107_flux, ap_index=ap_index,
    )


def cascade(altitude_km, target_mass_kg=260.0, projectile_mass_kg=1.0,
            rel_velocity_km_s=10.0, f107_flux=150.0, lc_min_m=0.1) -> Dict[str, Any]:
    """VLEO drag-limited transient cascade-window assessment (Phase 3).

    NASA SBM fragment count + per-size drag clearing -> self-cleaning timeline.
    """
    if not _VLEO_OK:
        return {"error": "vleo module not loaded", "load_error": _VLEO_ERR}
    return _vleo.assess_vleo_cascade(
        altitude_km=altitude_km, target_mass_kg=target_mass_kg,
        projectile_mass_kg=projectile_mass_kg, rel_velocity_km_s=rel_velocity_km_s,
        f107_flux=f107_flux, lc_min_m=lc_min_m,
    )


def fuel_tradespace(altitude_km, target_separation_m=1000.0, ballistic_coef=None,
                    f107_flux=150.0, ap_index=15.0, forecast_days=30.0) -> Dict[str, Any]:
    """VLEO combined drag-makeup + collision-avoidance fuel trade-space (Phase 3)."""
    if not _VLEO_OK:
        return {"error": "vleo module not loaded", "load_error": _VLEO_ERR}
    bc = ballistic_coef if ballistic_coef is not None else _vleo.DEFAULT_BALLISTIC_COEF
    return _vleo.vleo_fuel_tradespace(
        altitude_km=altitude_km, target_separation_m=target_separation_m,
        ballistic_coef=bc, f107_flux=f107_flux, ap_index=ap_index,
        forecast_days=forecast_days,
    )
