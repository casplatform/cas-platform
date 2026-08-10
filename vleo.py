#!/usr/bin/env python3
"""
CAS VLEO Module — Phase 1
==========================
Drag-aware physics layer for Very Low Earth Orbit decision support.
Standalone module — does not modify cas_engine.py.

Features:
  - Regime detection (LEO vs VLEO)
  - USSA76 exponential atmosphere density model
  - Drag acceleration computation
  - Drag-induced position uncertainty inflation
  - Drag-aware orbit propagation (Cowell method with RK4)
  - Orbital lifetime estimation

References:
  - US Standard Atmosphere 1976 (USSA76)
  - Vallado, "Fundamentals of Astrodynamics and Applications", 4th Ed.
  - ESA ECSS-E-ST-10-04C (Space Environment)

Author: CAS Platform
Version: 1.0 (Phase 1)
Date: May 2026
"""

import math
from typing import Tuple, Optional

# ═══════════════════════════════════════════════════
# CONSTANTS
# ═══════════════════════════════════════════════════
EARTH_RADIUS_KM = 6378.137
EARTH_MU_KM3S2 = 398600.4418          # GM, km³/s²
EARTH_MU_M3S2 = 3.986004418e14        # GM, m³/s²
EARTH_OMEGA = 7.2921159e-5            # rotation rate, rad/s

# Regime thresholds
REGIME_VLEO_MAX_KM = 450              # Below this = VLEO
REGIME_HYBRID_MIN_KM = 400            # 400-450 = hybrid zone

# Default spacecraft parameters
DEFAULT_BALLISTIC_COEF = 50.0         # kg/m² (B = m / (Cd * A))
DEFAULT_CD = 2.2                      # drag coefficient

# ═══════════════════════════════════════════════════
# USSA76 EXPONENTIAL ATMOSPHERE MODEL
# ═══════════════════════════════════════════════════
# Piecewise exponential: ρ(h) = ρ_base * exp(-(h - h_base) / H)
# Each tuple: (h_base_km, rho_base_kg_m3, scale_height_km)
# Source: Vallado Table 8-4 (simplified USSA76)
_ATMO_BANDS = [
    (100,  5.297e-7,   5.877),
    (110,  9.661e-8,   7.263),
    (120,  2.438e-8,   9.473),
    (130,  8.484e-9,  12.636),
    (140,  3.845e-9,  16.149),
    (150,  2.070e-9,  22.523),
    (180,  5.464e-10, 29.740),
    (200,  2.789e-10, 37.105),
    (250,  7.248e-11, 45.546),
    (300,  2.418e-11, 53.628),
    (350,  9.518e-12, 53.298),
    (400,  3.725e-12, 58.515),
    (450,  1.585e-12, 60.828),
    (500,  6.967e-13, 63.822),
    (600,  1.454e-13, 71.835),
    (700,  3.614e-14, 88.667),
    (800,  1.170e-14, 124.64),
    (900,  5.245e-15, 181.05),
    (1000, 3.019e-15, 268.00),
]


def detect_regime(altitude_km: float) -> str:
    """
    Classify orbital regime.

    Returns:
        'vleo'   — altitude < 450 km, drag-dominant
        'hybrid' — 400-450 km transition zone
        'leo'    — altitude >= 450 km, drag-negligible for conjunction
    """
    if altitude_km < REGIME_HYBRID_MIN_KM:
        return "vleo"
    elif altitude_km < REGIME_VLEO_MAX_KM:
        return "hybrid"
    return "leo"


def atmosphere_density(altitude_km: float) -> float:
    """
    Exponential atmosphere density from USSA76 simplified model.

    Args:
        altitude_km: Geodetic altitude in km (100-1000 km valid range)

    Returns:
        Atmospheric density in kg/m³

    Below 100 km or above 1000 km returns boundary values.
    """
    if altitude_km <= 0:
        return 0.0

    # Clamp to model range
    if altitude_km < 100:
        altitude_km = 100.0
    if altitude_km > 1000:
        # Above 1000 km, use last band with extrapolation
        h_base, rho_base, H = _ATMO_BANDS[-1]
        return rho_base * math.exp(-(altitude_km - h_base) / H)

    # Find the correct band
    band_idx = 0
    for i, (h_base, _, _) in enumerate(_ATMO_BANDS):
        if altitude_km >= h_base:
            band_idx = i
        else:
            break

    h_base, rho_base, H = _ATMO_BANDS[band_idx]
    return rho_base * math.exp(-(altitude_km - h_base) / H)


def drag_acceleration(
    pos_m: Tuple[float, float, float],
    vel_m_s: Tuple[float, float, float],
    ballistic_coef_kg_m2: float = DEFAULT_BALLISTIC_COEF,
    cd: float = DEFAULT_CD
) -> Tuple[float, float, float]:
    """
    Compute atmospheric drag acceleration vector.

    F_drag = -½ · ρ · |v_rel|² · Cd · (A/m) · v̂_rel
    a_drag = F_drag / m = -½ · ρ · |v_rel|² · (Cd / B) · v̂_rel

    where B = ballistic coefficient = m / (Cd * A)

    Args:
        pos_m: ECI position vector (x, y, z) in meters
        vel_m_s: ECI velocity vector (vx, vy, vz) in m/s
        ballistic_coef_kg_m2: Ballistic coefficient B = m/(Cd*A) in kg/m²
        cd: Drag coefficient (default 2.2)

    Returns:
        Drag acceleration vector (ax, ay, az) in m/s²
    """
    x, y, z = pos_m
    vx, vy, vz = vel_m_s

    # Altitude
    r = math.sqrt(x*x + y*y + z*z)
    alt_km = (r / 1000.0) - EARTH_RADIUS_KM

    if alt_km <= 0 or alt_km > 1000:
        return (0.0, 0.0, 0.0)

    # Atmospheric density
    rho = atmosphere_density(alt_km)
    if rho <= 0:
        return (0.0, 0.0, 0.0)

    # Velocity relative to atmosphere (co-rotating with Earth)
    # v_rel = v_sat - omega × r
    vrel_x = vx - (-EARTH_OMEGA * y)
    vrel_y = vy - (EARTH_OMEGA * x)
    vrel_z = vz

    v_rel_mag = math.sqrt(vrel_x**2 + vrel_y**2 + vrel_z**2)
    if v_rel_mag < 1e-6:
        return (0.0, 0.0, 0.0)

    # a_drag = -½ · ρ · v² · (Cd*A/m) · v̂
    # Cd*A/m = Cd / B  (since B = m/(Cd*A), so Cd*A/m = Cd²/(B*Cd) = Cd/B)
    # Actually: A/m = 1/(B/Cd) = Cd/B... no.
    # B = m/(Cd*A) → Cd*A/m = 1/B... wait.
    # B = m/(Cd*A), so Cd*A = m/B, so Cd*A/m = 1/B
    factor = -0.5 * rho * v_rel_mag / ballistic_coef_kg_m2

    ax = factor * vrel_x
    ay = factor * vrel_y
    az = factor * vrel_z

    return (ax, ay, az)


def gravity_acceleration(
    pos_m: Tuple[float, float, float]
) -> Tuple[float, float, float]:
    """
    Two-body gravitational acceleration (point mass Earth).

    Args:
        pos_m: ECI position (x, y, z) in meters

    Returns:
        Gravitational acceleration (ax, ay, az) in m/s²
    """
    x, y, z = pos_m
    r = math.sqrt(x*x + y*y + z*z)
    if r < 1e-3:
        return (0.0, 0.0, 0.0)

    r3 = r * r * r
    mu = EARTH_MU_M3S2
    return (-mu * x / r3, -mu * y / r3, -mu * z / r3)


def drag_sigma_inflation(
    altitude_km: float,
    kp_index: float = 3.0,
    f107_flux: float = 150.0
) -> float:
    """
    Position uncertainty multiplier for drag-driven TLE drift.

    In VLEO, atmospheric density uncertainty (±50-200% depending on
    solar activity) causes orbit prediction errors much larger than
    in LEO. This function returns a multiplier for the position
    uncertainty sigma used in Pc calculations.

    Args:
        altitude_km: Altitude in km
        kp_index: Geomagnetic Kp index (0-9, higher = more active)
        f107_flux: F10.7 solar radio flux (SFU, ~70 quiet, ~250 active)

    Returns:
        Sigma multiplier (1.0 = no inflation, >1.0 = increased uncertainty)

    Reference values:
        LEO (>450km): 1.0x (no inflation)
        VLEO 350-450km: 1.5-2.5x
        VLEO 250-350km: 2.5-5.0x
        VLEO <250km: 5.0-15.0x
    """
    regime = detect_regime(altitude_km)

    if regime == "leo":
        return 1.0

    # Base inflation from altitude
    if altitude_km >= 400:
        base = 1.5
    elif altitude_km >= 350:
        base = 2.0
    elif altitude_km >= 300:
        base = 3.0
    elif altitude_km >= 250:
        base = 5.0
    elif altitude_km >= 200:
        base = 8.0
    else:
        base = 12.0

    # Solar activity modifier
    # F10.7 ~ 70 (quiet) → 150 (moderate) → 250+ (active)
    solar_factor = 1.0 + 0.3 * max(0, (f107_flux - 100) / 100)

    # Geomagnetic activity modifier
    # Kp 0-2 quiet, 3-4 moderate, 5+ storm
    geo_factor = 1.0 + 0.2 * max(0, (kp_index - 3) / 2)

    return base * solar_factor * geo_factor


# ═══════════════════════════════════════════════════
# DRAG-AWARE ORBIT PROPAGATION (RK4 Cowell)
# ═══════════════════════════════════════════════════

def _state_derivative(state, bc):
    """RK4 state derivative: [vx, vy, vz, ax, ay, az]"""
    x, y, z, vx, vy, vz = state
    pos = (x, y, z)
    vel = (vx, vy, vz)

    # Gravity
    gx, gy, gz = gravity_acceleration(pos)

    # Drag
    dx, dy, dz = drag_acceleration(pos, vel, bc)

    return [vx, vy, vz, gx + dx, gy + dy, gz + dz]


def propagate_with_drag(
    pos_m: Tuple[float, float, float],
    vel_m_s: Tuple[float, float, float],
    dt_seconds: float,
    ballistic_coef: float = DEFAULT_BALLISTIC_COEF,
    steps: int = 0
) -> Tuple[Tuple[float, float, float], Tuple[float, float, float]]:
    """
    Propagate orbit with drag using RK4 integrator (Cowell method).

    Args:
        pos_m: Initial ECI position (x, y, z) in meters
        vel_m_s: Initial ECI velocity (vx, vy, vz) in m/s
        dt_seconds: Total propagation time in seconds
        ballistic_coef: Spacecraft ballistic coefficient kg/m²
        steps: Number of integration steps (0 = auto, ~30s per step)

    Returns:
        (final_pos_m, final_vel_m_s) tuple
    """
    if abs(dt_seconds) < 1e-6:
        return pos_m, vel_m_s

    # Auto step size: ~30 seconds per step for VLEO accuracy
    if steps <= 0:
        steps = max(1, int(abs(dt_seconds) / 30.0))

    h = dt_seconds / steps
    state = [pos_m[0], pos_m[1], pos_m[2],
             vel_m_s[0], vel_m_s[1], vel_m_s[2]]

    for _ in range(steps):
        k1 = _state_derivative(state, ballistic_coef)
        s2 = [state[i] + 0.5 * h * k1[i] for i in range(6)]
        k2 = _state_derivative(s2, ballistic_coef)
        s3 = [state[i] + 0.5 * h * k2[i] for i in range(6)]
        k3 = _state_derivative(s3, ballistic_coef)
        s4 = [state[i] + h * k3[i] for i in range(6)]
        k4 = _state_derivative(s4, ballistic_coef)

        for i in range(6):
            state[i] += (h / 6.0) * (k1[i] + 2*k2[i] + 2*k3[i] + k4[i])

    return (
        (state[0], state[1], state[2]),
        (state[3], state[4], state[5])
    )


def estimate_orbital_lifetime(
    altitude_km: float,
    ballistic_coef: float = DEFAULT_BALLISTIC_COEF,
    f107_flux: float = 150.0,
    min_altitude_km: float = 120.0
) -> float:
    """
    Estimate remaining orbital lifetime in days.

    Uses simplified King-Hele decay formula:
    Δa/rev ≈ -2π · a · (ρ · a / B)

    This is a rough estimate — accuracy ±30-50% depending on solar cycle.

    Args:
        altitude_km: Current altitude in km
        ballistic_coef: B = m/(Cd*A) in kg/m²
        f107_flux: F10.7 solar flux for density scaling
        min_altitude_km: Altitude threshold for reentry

    Returns:
        Estimated lifetime in days (0 if already below threshold)
    """
    if altitude_km <= min_altitude_km:
        return 0.0

    # Iterative decay: step down in 1km increments
    total_seconds = 0.0
    h = altitude_km

    while h > min_altitude_km:
        a = (EARTH_RADIUS_KM + h) * 1000.0  # semi-major axis in meters
        rho = atmosphere_density(h)

        # Solar activity scaling (simplified)
        if f107_flux > 150:
            rho *= 1.0 + 0.5 * (f107_flux - 150) / 100
        elif f107_flux < 100:
            rho *= max(0.3, f107_flux / 100)

        # Orbital period
        T = 2 * math.pi * math.sqrt(a**3 / EARTH_MU_M3S2)

        # Decay per orbit: Δa = -2π · ρ · a² / B
        da_per_orbit = 2 * math.pi * rho * a * a / ballistic_coef

        if da_per_orbit < 1e-10:
            # Negligible drag — effectively infinite lifetime
            return total_seconds / 86400.0 + 36500  # cap at 100 years

        # How many orbits to drop 1 km (1000 m)?
        orbits_per_km = 1000.0 / da_per_orbit
        time_for_1km = orbits_per_km * T

        total_seconds += time_for_1km
        h -= 1.0

        # Safety cap: >100 years
        if total_seconds > 3.1536e9:
            return total_seconds / 86400.0

    return total_seconds / 86400.0


def altitude_from_state(pos_m: Tuple[float, float, float]) -> float:
    """
    Compute geodetic altitude from ECI position.

    Args:
        pos_m: ECI position (x, y, z) in meters

    Returns:
        Altitude in km
    """
    r = math.sqrt(pos_m[0]**2 + pos_m[1]**2 + pos_m[2]**2)
    return (r / 1000.0) - EARTH_RADIUS_KM


def orbital_velocity(altitude_km: float) -> float:
    """
    Circular orbital velocity at given altitude.

    Args:
        altitude_km: Altitude in km

    Returns:
        Velocity in m/s
    """
    r = (EARTH_RADIUS_KM + altitude_km) * 1000.0
    return math.sqrt(EARTH_MU_M3S2 / r)


def orbital_period(altitude_km: float) -> float:
    """
    Orbital period at given altitude.

    Args:
        altitude_km: Altitude in km

    Returns:
        Period in seconds
    """
    a = (EARTH_RADIUS_KM + altitude_km) * 1000.0
    return 2 * math.pi * math.sqrt(a**3 / EARTH_MU_M3S2)


def drag_delta_v_per_orbit(
    altitude_km: float,
    ballistic_coef: float = DEFAULT_BALLISTIC_COEF
) -> float:
    """
    Estimate ΔV lost to drag per orbit.

    Args:
        altitude_km: Altitude in km
        ballistic_coef: B = m/(Cd*A) in kg/m²

    Returns:
        ΔV per orbit in m/s
    """
    rho = atmosphere_density(altitude_km)
    v = orbital_velocity(altitude_km)
    T = orbital_period(altitude_km)

    # ΔV ≈ ½ · ρ · v² · (1/B) · v · T / v = ½ · ρ · v · T / B
    # Simplified: drag deceleration * orbit time
    a_drag = 0.5 * rho * v * v / ballistic_coef
    return a_drag * T


# ═══════════════════════════════════════════════════
# VLEO DECISION SUPPORT
# ═══════════════════════════════════════════════════

def vleo_conjunction_assessment(
    altitude_km: float,
    miss_distance_m: float,
    pc_standard: float,
    kp_index: float = 3.0,
    f107_flux: float = 150.0,
    time_to_tca_hours: float = 24.0,
    ballistic_coef: float = DEFAULT_BALLISTIC_COEF,
    forecast_days: float = 30.0,
    ap_index=None,
    date=None,
) -> dict:
    """
    Enhanced conjunction assessment for VLEO objects (Phase 1 + Phase 2).

    Phase 1: drag-induced uncertainty inflation, lifetime, density.
    Phase 2: NRLMSIS 2.1 density (model-tagged), ΔV drag-makeup forecast,
             and an operational urgency score.

    IS:     decision-support context (uncertainty, ΔV budget, triage priority).
    IS NOT: an autonomous maneuver decision. The deterministic Pc and human
            judgement remain authoritative; this only informs the operator.

    Args:
        altitude_km: Object altitude (km)
        miss_distance_m: Predicted miss distance (m)
        pc_standard: Standard Pc computed by the CAS engine
        kp_index: Current Kp index (used for Phase-1 sigma inflation)
        f107_flux: Current F10.7 flux
        time_to_tca_hours: Hours until TCA (urgency time axis)
        ballistic_coef: B = m/(Cd*A) in kg/m² (ΔV forecast)
        forecast_days: ΔV forecast horizon (days)
        ap_index: Geomagnetic Ap (MSIS/urgency). If None, derived from kp_index.
        date: numpy.datetime64/datetime for MSIS (None → fixed epoch)

    Returns:
        dict with VLEO-enhanced assessment (Phase 1 fields + Phase 2 block)
    """
    regime = detect_regime(altitude_km)
    sigma_mult = drag_sigma_inflation(altitude_km, kp_index, f107_flux)
    lifetime = estimate_orbital_lifetime(altitude_km)
    dv_per_orbit = drag_delta_v_per_orbit(altitude_km)  # Phase-1 form (kept)

    # Ap from Kp if not supplied (inverse of the urgency table, midpoints).
    if ap_index is None:
        _kp_to_ap = {0:0,1:4,2:7,3:15,4:27,5:48,6:80,7:132,8:207,9:400}
        ap_index = _kp_to_ap.get(int(round(kp_index)), 15)

    # Phase-2 NRLMSIS 2.1 density (model-tagged) for the headline density.
    rho_msis, density_model = atmosphere_density_msis(
        altitude_km, date=date, f107=f107_flux, ap=ap_index)

    # Phase-2 ΔV drag-makeup forecast.
    dv_forecast = drag_delta_v_forecast(
        altitude_km, ballistic_coef=ballistic_coef, forecast_days=forecast_days,
        f107=f107_flux, ap=ap_index, date=date)

    # Phase-2 operational urgency score.
    urgency = vleo_urgency_score(
        altitude_km, miss_distance_m, pc_standard, time_to_tca_hours,
        f107=f107_flux, ap=ap_index)

    return {
        "regime": regime,
        "altitude_km": round(altitude_km, 1),
        "sigma_inflation": round(sigma_mult, 2),
        "atmosphere_density_kg_m3": f"{rho_msis:.3e}",
        "density_model": density_model,
        "pc_standard": pc_standard,
        "pc_confidence_note": (
            "High confidence — standard LEO analysis applies"
            if regime == "leo" else
            f"Reduced confidence — position uncertainty inflated {sigma_mult:.1f}x due to drag. "
            f"Predicted miss distance has higher uncertainty at {altitude_km:.0f}km."
        ),
        "estimated_lifetime_days": round(lifetime, 1),
        "drag_dv_per_orbit_ms": round(dv_per_orbit, 4),
        # ── Phase 2 ──
        "urgency": urgency,
        "delta_v_forecast": dv_forecast,
        "decision_label": (
            f"Standard LEO analysis — urgency {urgency['urgency_score']}/100 "
            f"[{urgency['band']}]" if regime == "leo" else
            f"VLEO drag-aware assessment — urgency {urgency['urgency_score']}/100 "
            f"[{urgency['band']}]"
        ),
        "recommendation": (
            None if regime == "leo" else
            f"VLEO object ({urgency['band']}): drag inflates position uncertainty "
            f"{sigma_mult:.1f}x; predicted geometry may shift before TCA. "
            f"Drag-makeup budget ~{dv_forecast['daily_dv_ms']:.2f} m/s/day at this altitude. "
            f"Operator decision required — re-evaluate as TCA approaches. "
            f"(Decision support only; not an autonomous maneuver recommendation.)"
        ),
    }


# ═══════════════════════════════════════════════════
# SELF-TEST
# ═══════════════════════════════════════════════════

# ═══════════════════════════════════════════════════
# PHASE 2 — NRLMSIS 2.1 + ΔV FORECAST + URGENCY SCORE
# ═══════════════════════════════════════════════════
# Phase 2 extends Phase 1 without breaking any existing function.
#   1. NRLMSIS 2.1 atmosphere (solar/geomagnetic aware) — optional pymsis,
#      graceful USSA76+solar-scaling fallback. Output is always model-tagged.
#   2. Drag-makeup ΔV forecast — cumulative budget over N days.
#      ΔV_rev = π · (Cd·A/m) · a · ρ · V   (ESA arXiv:1910.05091 eq.31;
#      USPTO 9966658 eq.5). With B=m/(Cd·A): ΔV_rev = π · a · ρ · V / B.
#   3. VLEO urgency score (0–100) — operational triage signal.
#
# IS:     decision-support metadata (priority / uncertainty / ΔV context).
# IS NOT: an autonomous maneuver decision. Human-in-the-loop is preserved.

# Optional NRLMSIS 2.1 backend; USSA76+solar-scaling fallback if absent.
try:
    import pymsis as _pymsis
    _MSIS_AVAILABLE = True
except Exception:
    _pymsis = None
    _MSIS_AVAILABLE = False


def _ussa76_solar_scaled(altitude_km, f107=150.0, ap=15.0):
    """Fallback: static USSA76 profile analytically scaled by F10.7/Ap.
    USSA76 ≈ average solar (F10.7≈150). Sensitivity to solar/geomagnetic
    activity GROWS with altitude (≈0 at 200 km → ~1 at 500 km), matching
    NRLMSIS behaviour. Coarse analogue, NOT a substitute; used only when
    pymsis is absent."""
    rho0 = atmosphere_density(altitude_km)
    if rho0 <= 0:
        return rho0
    w = max(0.0, min(1.0, (altitude_km - 200.0) / 300.0))
    f_factor = 1.0 + w * 0.6 * (f107 - 150.0) / 100.0
    a_factor = 1.0 + w * 0.5 * max(0.0, (ap - 15.0)) / 65.0
    return rho0 * max(0.1, f_factor) * a_factor


def atmosphere_density_msis(altitude_km, date=None, lat=0.0, lon=0.0,
                            f107=150.0, f107a=150.0, ap=15.0):
    """Solar/geomagnetic-aware density. NRLMSIS 2.1 via pymsis when available,
    else USSA76+solar-scaling. Returns (density_kg_m3, model_label)."""
    if altitude_km <= 0:
        return 0.0, ("NRLMSIS 2.1" if _MSIS_AVAILABLE else "USSA76+solar-scaled")
    if _MSIS_AVAILABLE:
        try:
            import numpy as _np
            if date is None:
                date = _np.datetime64("2026-01-01T12:00")
            out = _pymsis.calculate(date, lon, lat, altitude_km,
                                    f107, f107a, [[ap] * 7])
            rho = float(_np.asarray(out).reshape(-1)[0])
            if rho == rho and rho > 0:
                return rho, "NRLMSIS 2.1"
        except Exception:
            pass
    return _ussa76_solar_scaled(altitude_km, f107, ap), "USSA76+solar-scaled"


def drag_delta_v_per_orbit_v2(altitude_km, ballistic_coef=DEFAULT_BALLISTIC_COEF,
                              cd=DEFAULT_CD, f107=150.0, ap=15.0, date=None):
    """ΔV lost to drag per orbit, literature form: ΔV_rev = π·a·ρ·V/B.
    Uses MSIS density when available. Phase-1 drag_delta_v_per_orbit is kept
    unchanged for backward compatibility; this is the standards-aligned form."""
    rho, _ = atmosphere_density_msis(altitude_km, date=date, f107=f107, ap=ap)
    if rho <= 0:
        return 0.0
    a = (EARTH_RADIUS_KM + altitude_km) * 1000.0
    v = orbital_velocity(altitude_km)
    return math.pi * a * rho * v / ballistic_coef


def drag_delta_v_forecast(altitude_km, ballistic_coef=DEFAULT_BALLISTIC_COEF,
                          forecast_days=30.0, cd=DEFAULT_CD,
                          f107=150.0, ap=15.0, date=None):
    """Cumulative drag-makeup ΔV budget over a horizon, plus unmaintained
    decay path for context. In VLEO, drag compensation is typically the
    DOMINANT term in the mission ΔV budget (ESA arXiv:1910.05091)."""
    rho, model = atmosphere_density_msis(altitude_km, date=date, f107=f107, ap=ap)
    T = orbital_period(altitude_km)
    per_orbit = drag_delta_v_per_orbit_v2(altitude_km, ballistic_coef, cd,
                                          f107, ap, date)
    orbits_per_day = 86400.0 / T if T > 0 else 0.0
    daily = per_orbit * orbits_per_day
    horizon_s = forecast_days * 86400.0
    orbits_in_horizon = horizon_s / T if T > 0 else 0.0
    total_dv = per_orbit * orbits_in_horizon

    # Unmaintained decay path (informational), ≤6h steps.
    decay_alt = altitude_km
    t_acc = 0.0
    step = min(horizon_s, 21600.0) if horizon_s > 0 else 0.0
    guard = 0
    while step > 0 and t_acc < horizon_s and decay_alt > 120.0 and guard < 100000:
        guard += 1
        r, _m = atmosphere_density_msis(decay_alt, date=date, f107=f107, ap=ap)
        a_m = (EARTH_RADIUS_KM + decay_alt) * 1000.0
        Tk = orbital_period(decay_alt)
        da_per_orbit = 2 * math.pi * r * a_m * a_m / ballistic_coef
        orbits_in_step = step / Tk if Tk > 0 else 0.0
        decay_alt -= (da_per_orbit * orbits_in_step) / 1000.0
        t_acc += step

    return {
        "altitude_km": round(altitude_km, 1),
        "horizon_days": round(forecast_days, 1),
        "ballistic_coef": ballistic_coef,
        "per_orbit_dv_ms": round(per_orbit, 5),
        "daily_dv_ms": round(daily, 4),
        "total_dv_ms": round(total_dv, 3),
        "orbits_in_horizon": round(orbits_in_horizon, 1),
        "decay_altitude_km": round(max(decay_alt, 0.0), 1),
        "decay_drop_km": round(altitude_km - max(decay_alt, 0.0), 2),
        "density_model": model,
        "note": ("Drag-makeup dominates VLEO budgets. Physics estimate "
                 "(+/-30-50% with solar cycle), NOT a propellant guarantee."),
    }


def _ap_to_kp(ap):
    """Rough Ap->Kp inversion for the urgency drift axis (triage weight only)."""
    table = [(0,0),(2,0.33),(3,0.67),(4,1),(5,1.33),(6,1.67),(7,2),(9,2.33),
             (12,2.67),(15,3),(18,3.33),(22,3.67),(27,4),(32,4.33),(39,4.67),
             (48,5),(56,5.33),(67,5.67),(80,6),(94,6.33),(111,6.67),(132,7),
             (154,7.33),(179,7.67),(207,8),(236,8.33),(300,8.67),(400,9)]
    kp = 0.0
    for a_thr, k in table:
        if ap >= a_thr:
            kp = k
        else:
            break
    return kp


def vleo_urgency_score(altitude_km, miss_distance_m, pc, time_to_tca_hours,
                       f107=150.0, ap=15.0):
    """Operational urgency score (0-100) fusing Pc, geometry drift (drag),
    time-to-TCA and miss distance.
    IS: a triage SIGNAL. IS NOT: a maneuver decision / Pc replacement /
    autonomous trigger. Deterministic Pc and human judgement stay authoritative."""
    import math as _m
    if pc and pc > 0:
        lp = _m.log10(pc)
        pc_comp = max(0.0, min(45.0, (lp + 7.0) / 4.0 * 45.0))
    else:
        pc_comp = 0.0
    tt = max(0.0, time_to_tca_hours)
    if tt <= 6:
        time_comp = 25.0
    elif tt >= 72:
        time_comp = 0.0
    else:
        time_comp = 25.0 * (72.0 - tt) / 66.0
    sigma_mult = drag_sigma_inflation(altitude_km, kp_index=_ap_to_kp(ap),
                                      f107_flux=f107)
    drift_comp = max(0.0, min(20.0, (sigma_mult - 1.0) / 4.0 * 20.0))
    if miss_distance_m is not None and miss_distance_m > 0:
        miss_comp = max(0.0, min(10.0, (_m.log10(5000.0) - _m.log10(
            max(miss_distance_m, 50.0))) /
            (_m.log10(5000.0) - _m.log10(100.0)) * 10.0))
    else:
        miss_comp = 0.0
    score = round(max(0.0, min(100.0, pc_comp + time_comp + drift_comp + miss_comp)), 1)
    band = ("CRITICAL" if score >= 70 else "HIGH" if score >= 45 else
            "ELEVATED" if score >= 20 else "ROUTINE")
    return {
        "urgency_score": score,
        "band": band,
        "components": {"pc": round(pc_comp, 1), "time": round(time_comp, 1),
                       "geometry_drift": round(drift_comp, 1),
                       "miss_distance": round(miss_comp, 1)},
        "drivers": {"sigma_inflation": round(sigma_mult, 2),
                    "time_to_tca_hours": round(tt, 1),
                    "regime": detect_regime(altitude_km)},
        "is": "operator prioritisation signal (triage)",
        "is_not": "a maneuver decision, Pc replacement, or autonomous trigger",
    }


# ============================================================================
# PHASE 3 — Continuous Maneuver Simulation (drag-compensation station-keeping)
# ----------------------------------------------------------------------------
# Models continuous low-thrust drag compensation for VLEO assets, the approach
# flown by ESA's GOCE (ion propulsion) and SpaceX Starlink V3 (~350 km, electric
# propulsion). Answers an operator question: "to hold this orbit against drag,
# how much continuous thrust / propellant does it cost, and what does the
# altitude profile look like under a given compensation policy?"
#
# References:
#   - Garulli, Giannitrapani, Leomanni et al. (2011) "Autonomous Low-Earth-Orbit
#     Station-Keeping with Electric Propulsion"
#   - Leomanni et al. (2020) Acta Astronautica 167:460-466
#   - ESA GOCE drag-compensation operations (Steiger et al. 2014)
#
# IS:     a station-keeping propellant / thrust simulation (decision support)
# IS NOT: thruster hardware design, an autonomous control loop, or a propellant
#         guarantee. Operator decides the policy; this quantifies its cost.
# ============================================================================

def _thrust_acceleration_compensation(vel, drag_accel, compensation):
    """Continuous drag-compensation thrust acceleration.

    A drag-compensation thruster cancels `compensation` fraction of the
    instantaneous drag acceleration. Physically the correct direction is the
    exact opposite of the drag vector (drag acts along -v_rel, where v_rel is
    velocity relative to the co-rotating atmosphere), so thrust = -k * a_drag.
    At compensation=1.0 drag is fully cancelled and the orbit is held; the
    `vel` argument is retained for interface symmetry but the direction comes
    from the drag vector itself (matching drag_acceleration's v_rel frame).
    """
    return (-compensation * drag_accel[0],
            -compensation * drag_accel[1],
            -compensation * drag_accel[2])


def _state_derivative_thrust(state, bc, compensation, net_drag=None):
    """RK4 derivative with gravity + (drag + compensation thrust).

    Gravity is evaluated at each sub-step (cheap, position-sensitive). The net
    aerodynamic term (drag + compensation thrust) changes negligibly over a
    30-60s step (altitude varies ~0.2 km), so it is computed ONCE at the step
    start and passed in as `net_drag` — a 4-5x speedup with no meaningful
    accuracy loss. If net_drag is None we fall back to per-call evaluation.
    """
    x, y, z, vx, vy, vz = state
    pos = (x, y, z)
    if net_drag is None:
        vel = (vx, vy, vz)
        dx, dy, dz = drag_acceleration(pos, vel, bc)
        tx, ty, tz = _thrust_acceleration_compensation(vel, (dx, dy, dz), compensation)
        net_drag = (dx + tx, dy + ty, dz + tz)
    gx, gy, gz = gravity_acceleration(pos)
    return [vx, vy, vz, gx + net_drag[0], gy + net_drag[1], gz + net_drag[2]]


def simulate_continuous_maneuver(altitude_km,
                                 ballistic_coef=DEFAULT_BALLISTIC_COEF,
                                 duration_days=30.0,
                                 compensation=1.0,
                                 spacecraft_mass_kg=None,
                                 f107_flux=150.0,
                                 ap_index=15.0,
                                 samples=30):
    """Simulate continuous drag-compensation station-keeping over a horizon.

    Propagates a circular VLEO orbit with RK4 (gravity + drag + continuous
    prograde thrust offsetting `compensation` fraction of drag), tracking the
    altitude profile and accumulating the thruster delta-v.

    compensation: 1.0 full hold, 0.5 partial, 0.0 ballistic (recovers Phase-2 decay)
    """
    regime = detect_regime(altitude_km)
    # Density-model tag (consistent with Phase 2 NRLMSIS 2.1 / fallback).
    _, density_model = atmosphere_density_msis(altitude_km, f107=f107_flux, ap=ap_index)

    # Initial circular orbit state in ECI (equatorial, x-axis ascending node).
    r0 = (EARTH_RADIUS_KM + altitude_km) * 1000.0
    v0 = math.sqrt(EARTH_MU_M3S2 / r0)
    state = [r0, 0.0, 0.0, 0.0, v0, 0.0]

    total_seconds = duration_days * 86400.0
    # RK4 sub-step must stay small vs the orbital period (~5500s in VLEO) or
    # numerical energy loss masquerades as decay. We cap the step at 60s and
    # the step count at 200k (handles ~140-day horizons at 60s); longer
    # horizons relax the step but warn via coarse_step flag.
    step_dt = 60.0
    n_steps = int(total_seconds / step_dt)
    MAX_STEPS = 200000
    coarse_step = False
    if n_steps > MAX_STEPS:
        n_steps = MAX_STEPS
        step_dt = total_seconds / n_steps
        coarse_step = step_dt > 60.0  # flag if accuracy degraded
    n_steps = max(samples, n_steps)
    h = step_dt
    sample_every = max(1, n_steps // samples)

    profile = []
    cumulative_dv = 0.0
    thrust_accel_sum = 0.0
    thrust_accel_count = 0
    t_elapsed = 0.0

    for i in range(n_steps):
        cur_vel = (state[3], state[4], state[5])
        cur_pos = (state[0], state[1], state[2])
        # Compute drag + compensation thrust ONCE per step (held across RK4
        # sub-stages — drag is ~constant over 30-60s). This is the dominant cost.
        d_acc = drag_acceleration(cur_pos, cur_vel, ballistic_coef)
        t_acc = _thrust_acceleration_compensation(cur_vel, d_acc, compensation)
        net_drag = (d_acc[0] + t_acc[0], d_acc[1] + t_acc[1], d_acc[2] + t_acc[2])
        t_acc_mag = math.sqrt(t_acc[0]**2 + t_acc[1]**2 + t_acc[2]**2)
        cumulative_dv += t_acc_mag * h
        thrust_accel_sum += t_acc_mag
        thrust_accel_count += 1

        k1 = _state_derivative_thrust(state, ballistic_coef, compensation, net_drag)
        s2 = [state[j] + 0.5*h*k1[j] for j in range(6)]
        k2 = _state_derivative_thrust(s2, ballistic_coef, compensation, net_drag)
        s3 = [state[j] + 0.5*h*k2[j] for j in range(6)]
        k3 = _state_derivative_thrust(s3, ballistic_coef, compensation, net_drag)
        s4 = [state[j] + h*k3[j] for j in range(6)]
        k4 = _state_derivative_thrust(s4, ballistic_coef, compensation, net_drag)
        for j in range(6):
            state[j] += (h/6.0) * (k1[j] + 2*k2[j] + 2*k3[j] + k4[j])

        t_elapsed += h
        if i % sample_every == 0 or i == n_steps - 1:
            alt = altitude_from_state((state[0], state[1], state[2]))
            profile.append({"day": round(t_elapsed/86400.0, 2), "altitude_km": round(alt, 3)})

    final_alt = altitude_from_state((state[0], state[1], state[2]))
    net_change = final_alt - altitude_km
    avg_thrust_accel = (thrust_accel_sum / thrust_accel_count) if thrust_accel_count else 0.0
    avg_thrust_mN = None
    if spacecraft_mass_kg is not None and spacecraft_mass_kg > 0:
        avg_thrust_mN = round(spacecraft_mass_kg * avg_thrust_accel * 1000.0, 4)
    daily_dv = cumulative_dv / duration_days if duration_days > 0 else 0.0

    if compensation >= 0.999:
        policy, policy_note = "full_hold", "Full drag compensation — altitude held; thruster cancels all drag loss."
    elif compensation <= 1e-6:
        policy, policy_note = "ballistic", "No thrust (ballistic) — natural decay; matches Phase-2 lifetime physics."
    else:
        policy, policy_note = "partial", f"Partial compensation ({compensation:.0%}) — altitude decays slower than ballistic."

    return {
        "regime": regime,
        "initial_altitude_km": round(altitude_km, 3),
        "final_altitude_km": round(final_alt, 3),
        "net_altitude_change_km": round(net_change, 3),
        "duration_days": round(duration_days, 2),
        "compensation_fraction": round(compensation, 4),
        "policy": policy,
        "policy_note": policy_note,
        "thrust_delta_v_ms": round(cumulative_dv, 4),
        "daily_thrust_delta_v_ms": round(daily_dv, 5),
        "avg_thrust_acceleration_m_s2": round(avg_thrust_accel, 9),
        "avg_thrust_force_mN": avg_thrust_mN,
        "ballistic_coef": ballistic_coef,
        "density_model": density_model,
        "altitude_profile": profile,
        "integration_step_s": round(h, 2),
        "coarse_step_warning": coarse_step,
        "is": "continuous drag-compensation station-keeping simulation (decision support)",
        "is_not": "thruster hardware design, an autonomous control loop, or a propellant guarantee",
        "note": ("Specific delta-v budget to hold orbit against drag. Physics estimate "
                 "(+/-30-50% with solar cycle and attitude/area variation), NOT a propellant guarantee."),
    }


# ============================================================================
# PHASE 3 — VLEO Cascade Awareness (self-cleaning debris-cloud timeline)
# ----------------------------------------------------------------------------
# A fragmentation in VLEO does NOT trigger a classical, persistent Kessler
# cascade: atmospheric drag clears the debris cloud on a timescale of months
# to a few years (vs centuries at 700-1000 km). What it DOES create is a
# TRANSIENT elevated-risk window while the cloud decays, plus a brief
# downward sweep as eccentric fragments cross lower altitude bands.
#
# This module quantifies that window using:
#   - NASA Standard Breakup Model (SBM) for the fragment size distribution:
#       N_cum(L_c) = 0.1 * M^0.75 * L_c^-1.71   (catastrophic collision)
#     Catastrophic threshold: specific energy >= 40 J/g (Ek_projectile / M_target).
#   - Per-size-class area-to-mass -> ballistic coefficient -> drag lifetime
#     (re-using estimate_orbital_lifetime).
#
# References:
#   - Johnson et al. (2001) "NASA's new breakup model of EVOLVE 4.0", Adv. Space Res.
#   - NASA Standard Breakup Model 2000/2011 revision (catastrophic = 40 J/g)
#   - ESA Space Environment Report 2025 (drag self-cleaning below ~600 km)
#
# IS:     a drag-limited transient cascade-window estimate (self-cleaning).
# IS NOT: a full NASA EVOLVE/LEGEND breakup simulation, per-fragment orbit
#         propagation, or a claim of persistent Kessler instability in VLEO.
# ============================================================================

# NASA SBM catastrophic-collision specific-energy threshold.
SBM_CATASTROPHIC_J_PER_G = 40.0
# SBM characteristic-length size classes (m) for the clearing breakdown.
_SBM_SIZE_CLASSES_M = [0.01, 0.05, 0.1, 0.5, 1.0]  # 1cm, 5cm, 10cm, 50cm, 1m


def sbm_fragment_count(collision_mass_kg, lc_min_m=0.1, event="collision"):
    """NASA SBM cumulative fragment count larger than characteristic length lc_min.

    N_cum(L_c) = 0.1 * M^0.75 * L_c^-1.71   (collision)
                 6   * s * L_c^-1.6         (explosion; s~scaling, approximated)

    Args:
        collision_mass_kg: total fragmenting mass M (kg)
        lc_min_m: minimum characteristic length to count (m), default 0.1 m (10 cm,
                  the rough trackable-size floor)
        event: "collision" or "explosion"

    Returns:
        cumulative fragment count (>= lc_min_m)
    """
    if collision_mass_kg <= 0 or lc_min_m <= 0:
        return 0.0
    if event == "explosion":
        # Explosion power law (NASA SBM): N = 6 * L_c^-1.6, scaled by mass proxy.
        # Mass scaling for explosions is event-specific; use a conservative
        # unit-scale (s=1) — explosions are secondary to collisions in VLEO risk.
        return 6.0 * (lc_min_m ** -1.6)
    # Collision (catastrophic) power law.
    return 0.1 * (collision_mass_kg ** 0.75) * (lc_min_m ** -1.71)


def is_catastrophic_collision(projectile_mass_kg, target_mass_kg, rel_velocity_km_s):
    """NASA SBM catastrophic test: Ek_projectile / M_target >= 40 J/g.

    Returns (is_catastrophic, specific_energy_j_per_g).
    """
    if target_mass_kg <= 0:
        return False, 0.0
    v_ms = rel_velocity_km_s * 1000.0
    # Kinetic energy of projectile (J) = 0.5 * m * v^2
    ek_j = 0.5 * projectile_mass_kg * v_ms * v_ms
    # Specific energy per gram of target mass.
    specific_j_per_g = ek_j / (target_mass_kg * 1000.0)
    return specific_j_per_g >= SBM_CATASTROPHIC_J_PER_G, specific_j_per_g


def _lc_to_area_to_mass(lc_m):
    """Approximate area-to-mass ratio (m^2/kg) for a fragment of characteristic
    length L_c, via the SBM-consistent scaling m ~ k * A^1.86 inverted to a
    representative A/m. Small fragments have HIGH A/m (decay fast); large
    fragments LOW A/m (decay slow). This is a representative central value;
    the true SBM samples a broad distribution per size bin.
    """
    # Representative effective area ~ Lc^2 (compact fragment), density-driven.
    # Empirical SBM A/m central trend: smaller Lc -> larger A/m.
    # Use a log-consistent fit anchored to typical observed values:
    #   1 cm  -> ~0.7 m^2/kg, 10 cm -> ~0.1 m^2/kg, 1 m -> ~0.02 m^2/kg
    if lc_m <= 0:
        return 0.1
    am = 0.073 * (lc_m ** -0.74)
    return max(0.005, min(am, 2.0))


def _area_to_mass_to_ballistic(area_to_mass_m2_kg, cd=DEFAULT_CD):
    """Convert A/m (m^2/kg) to ballistic coefficient B = m/(Cd*A) = 1/(Cd*(A/m))."""
    if area_to_mass_m2_kg <= 0:
        return DEFAULT_BALLISTIC_COEF
    return 1.0 / (cd * area_to_mass_m2_kg)


def assess_vleo_cascade(altitude_km,
                        target_mass_kg=260.0,
                        projectile_mass_kg=1.0,
                        rel_velocity_km_s=10.0,
                        f107_flux=150.0,
                        lc_min_m=0.1):
    """Drag-limited transient cascade-window assessment for a VLEO fragmentation.

    Combines the NASA SBM (fragment count + size distribution) with drag
    lifetime per size class to produce a self-cleaning timeline: how many
    fragments, how long until the cloud clears (50/90/99%), and the transient
    elevated-risk window. Decision support — not a full breakup simulation.

    Args:
        altitude_km: fragmentation altitude (km)
        target_mass_kg: larger object mass (kg)
        projectile_mass_kg: smaller object mass (kg)
        rel_velocity_km_s: relative impact velocity (km/s), typical LEO ~10
        f107_flux: solar flux for drag density scaling
        lc_min_m: minimum fragment size counted (m)

    Returns:
        dict with catastrophic flag, fragment count, per-size clearing times,
        cloud-clearing milestones, transient risk window, affected bands.
    """
    regime = detect_regime(altitude_km)
    total_mass = target_mass_kg + projectile_mass_kg

    cat, spec_energy = is_catastrophic_collision(
        projectile_mass_kg, target_mass_kg, rel_velocity_km_s)
    # Fragmenting mass: catastrophic -> both objects; else projectile only.
    frag_mass = total_mass if cat else projectile_mass_kg
    n_fragments = sbm_fragment_count(frag_mass, lc_min_m=lc_min_m, event="collision")

    # Per-size-class clearing: lifetime of a representative fragment at each Lc.
    size_breakdown = []
    for lc in _SBM_SIZE_CLASSES_M:
        am = _lc_to_area_to_mass(lc)
        bc = _area_to_mass_to_ballistic(am)
        life_days = estimate_orbital_lifetime(altitude_km, ballistic_coef=bc,
                                              f107_flux=f107_flux)
        # Count of fragments in [lc, next class) — differential from cumulative.
        n_ge = sbm_fragment_count(frag_mass, lc_min_m=lc, event="collision")
        size_breakdown.append({
            "characteristic_length_m": lc,
            "area_to_mass_m2_kg": round(am, 4),
            "ballistic_coef_kg_m2": round(bc, 2),
            "decay_lifetime_days": round(life_days, 1),
            "cumulative_count_ge": round(n_ge, 1),
        })

    # Cloud-clearing milestones: small (high-A/m) fragments clear first, large last.
    # Use the smallest and largest representative lifetimes as the 99%/50% bounds.
    life_small = size_breakdown[0]["decay_lifetime_days"]   # 1 cm, fast
    life_large = size_breakdown[-1]["decay_lifetime_days"]  # 1 m, slow
    # 50% of (numerous, small) fragments clear near the small-fragment timescale;
    # 99% requires the slowest (large) fragments to decay.
    clearing = {
        "fifty_percent_days": round(life_small, 1),
        "ninety_percent_days": round(0.5 * (life_small + life_large), 1),
        "ninety_nine_percent_days": round(life_large, 1),
    }

    # Transient elevated-risk window: from event until ~90% cleared.
    risk_window_days = clearing["ninety_percent_days"]

    # Downward sweep: eccentric fragments transiently cross lower bands.
    # Conservative estimate: perigee can drop by the ejection delta-v converted
    # to altitude. Typical SBM ejection ~ tens-hundreds m/s; bound the sweep.
    sweep_low_km = max(120.0, altitude_km - 60.0)  # representative lower reach
    affected_bands = {
        "fragmentation_altitude_km": round(altitude_km, 1),
        "downward_sweep_to_km": round(sweep_low_km, 1),
        "note": "Eccentric fragments transiently cross bands between sweep floor and fragmentation altitude.",
    }

    return {
        "regime": regime,
        "altitude_km": round(altitude_km, 1),
        "catastrophic": cat,
        "specific_energy_j_per_g": round(spec_energy, 1),
        "catastrophic_threshold_j_per_g": SBM_CATASTROPHIC_J_PER_G,
        "fragmenting_mass_kg": round(frag_mass, 1),
        "estimated_fragment_count": round(n_fragments, 0),
        "fragment_size_floor_m": lc_min_m,
        "size_class_breakdown": size_breakdown,
        "cloud_clearing": clearing,
        "transient_risk_window_days": risk_window_days,
        "affected_bands": affected_bands,
        "self_cleaning": True,
        "density_model": atmosphere_density_msis(altitude_km, f107=f107_flux)[1],
        "is": "drag-limited transient cascade-window estimate (self-cleaning dynamics)",
        "is_not": ("a full NASA EVOLVE/LEGEND breakup simulation, per-fragment orbit "
                   "propagation, or a claim of persistent Kessler instability in VLEO"),
        "note": ("VLEO is self-cleaning: drag clears the cloud in months-to-years, not "
                 "centuries. Fragment counts use the NASA SBM power law; clearing times "
                 "use drag lifetime per size class. Order-of-magnitude decision support."),
    }


# ============================================================================
# PHASE 3 — Fuel Optimizer (VLEO combined drag-makeup + collision-avoidance)
# ----------------------------------------------------------------------------
# In VLEO, station-keeping (drag make-up) and collision-avoidance (CA) maneuvers
# are intertwined: the spacecraft is ALREADY spending prograde ΔV continuously
# to fight drag. This module builds a TRADE-SPACE (not a recommendation) for the
# operator: what a CA maneuver costs at different lead times, and the saving from
# combining it with the drag make-up the satellite is doing anyway.
#
# Physics (along-track CA, Clohessy-Wiltshire linearised relative motion):
#   An along-track ΔV applied t seconds before TCA grows the cross-track / radial
#   separation roughly as Δsep ≈ 3 · ΔV · t (the secular along-track-to-radial
#   coupling dominates for lead times of hours-days). Hence EARLY maneuvers buy
#   separation cheaply: the required ΔV for a target Δsep scales as ~Δsep/(3·t).
#
# References:
#   - Diserens, Lewis et al. — NorthStar two-step CAM decision/design framework (AMOS 2025)
#   - Kayhan Space CAM Suggestion Engine maneuver tradespace (AMOS 2023)
#   - Clohessy & Wiltshire (1960) relative-motion equations
#
# IS:     a fuel/timing trade-space for the operator (options + costs).
# IS NOT: an autonomous maneuver command, a "do this" recommendation, a
#         propellant guarantee, or maneuver execution. The operator decides.
# ============================================================================

def ca_delta_v_for_separation(target_separation_m, lead_time_hours):
    """Along-track CA ΔV (m/s) to achieve a target added separation at TCA.

    Clohessy-Wiltshire secular approximation: Δsep ≈ 3 · ΔV · t, so
    ΔV ≈ Δsep / (3 · t). Early maneuvers (large t) are cheap.

    Args:
        target_separation_m: desired ADDED miss distance at TCA (m)
        lead_time_hours: hours before TCA the maneuver is applied

    Returns:
        required along-track ΔV (m/s); large for very-late maneuvers
    """
    t_s = lead_time_hours * 3600.0
    if t_s <= 0:
        return float("inf")
    return target_separation_m / (3.0 * t_s)


def vleo_fuel_tradespace(altitude_km,
                         target_separation_m=1000.0,
                         ballistic_coef=DEFAULT_BALLISTIC_COEF,
                         lead_times_hours=None,
                         f107_flux=150.0,
                         ap_index=15.0,
                         forecast_days=30.0):
    """Combined drag-makeup + collision-avoidance fuel trade-space for VLEO.

    Returns, for several maneuver lead times, the CA ΔV needed to add
    `target_separation_m` of miss distance, alongside the drag make-up ΔV the
    satellite spends over the horizon — and the saving from aligning the CA burn
    with the prograde drag-makeup it performs anyway.

    Args:
        altitude_km: object altitude (km)
        target_separation_m: desired added separation at TCA (m)
        ballistic_coef: B = m/(Cd*A) (kg/m^2)
        lead_times_hours: list of lead times to evaluate (default 6h..72h)
        f107_flux, ap_index: density drivers
        forecast_days: drag-makeup budget horizon (days)

    Returns:
        dict with the CA-vs-lead-time trade, drag-makeup context, and the
        combined-budget saving. Operator chooses; nothing is executed.
    """
    regime = detect_regime(altitude_km)
    if lead_times_hours is None:
        lead_times_hours = [6, 12, 24, 48, 72]

    # Drag make-up budget over the horizon (Phase-2 forecast) for context.
    dm = drag_delta_v_forecast(altitude_km, ballistic_coef=ballistic_coef,
                               forecast_days=forecast_days, f107=f107_flux, ap=ap_index)
    drag_daily = dm.get("daily_dv_ms", 0.0)

    # CA ΔV at each lead time. Earlier = cheaper.
    trade = []
    for lt in lead_times_hours:
        ca_dv = ca_delta_v_for_separation(target_separation_m, lt)
        # Combined-budget saving: a CA maneuver is primarily an along-track /
        # radial burn to create separation; only a fraction aligns with the
        # prograde drag make-up the satellite spends anyway. We do NOT treat the
        # drag budget as fully "paying" for the CA burn (that would be double-
        # counting — drag make-up is already committed to holding altitude).
        # Realistic overlap: at most the prograde-aligned component of the CA
        # burn, capped at a conservative ALIGNMENT_FRACTION, and never more than
        # the drag make-up actually available in the lead window.
        ALIGNMENT_FRACTION = 0.30  # conservative prograde-overlap ceiling
        window_days = lt / 24.0
        drag_in_window = drag_daily * window_days
        # Reusable = aligned fraction of CA, bounded by available drag make-up.
        reusable = min(ALIGNMENT_FRACTION * ca_dv, drag_in_window)
        net_ca_dv = max(0.0, ca_dv - reusable)
        trade.append({
            "lead_time_hours": lt,
            "ca_delta_v_ms": round(ca_dv, 4),
            "drag_makeup_in_window_ms": round(drag_in_window, 4),
            "combined_net_ca_delta_v_ms": round(net_ca_dv, 4),
            "saving_ms": round(reusable, 4),
        })

    # Headline saving at a representative 24h lead time.
    rep = next((t for t in trade if t["lead_time_hours"] == 24), trade[0])

    return {
        "regime": regime,
        "altitude_km": round(altitude_km, 1),
        "target_separation_m": round(target_separation_m, 1),
        "ballistic_coef": ballistic_coef,
        "drag_makeup_daily_dv_ms": round(drag_daily, 4),
        "drag_makeup_horizon_days": round(forecast_days, 1),
        "drag_makeup_total_dv_ms": dm.get("total_dv_ms"),
        "tradespace": trade,
        "representative_24h": rep,
        "density_model": dm.get("density_model"),
        "is": "a fuel/timing trade-space for the operator (options + costs)",
        "is_not": ("an autonomous maneuver command, a recommendation, a propellant "
                   "guarantee, or maneuver execution"),
        "note": ("Earlier maneuvers buy separation more cheaply (ΔV ~ Δsep/(3·t), "
                 "Clohessy-Wiltshire). Combined budget reuses only the prograde-aligned "
                 "fraction (<=30%) of the CA burn against the drag make-up already spent — "
                 "not a free maneuver. Illustrative geometry; a specific "
                 "conjunction uses its real miss vector, TCA and relative velocity. "
                 "Physics estimate, NOT a propellant guarantee. Operator decides."),
    }


if __name__ == "__main__":
    print("=" * 60)
    print("  CAS VLEO Module — Self-Test")
    print("=" * 60)

    # Test regime detection
    print("\n[1] Regime Detection:")
    for alt in [200, 300, 350, 420, 450, 500, 800]:
        print(f"  {alt:4d} km → {detect_regime(alt)}")

    # Test atmosphere density
    print("\n[2] Atmosphere Density (USSA76):")
    for alt in [200, 250, 300, 350, 400, 450, 500, 600, 800]:
        rho = atmosphere_density(alt)
        print(f"  {alt:4d} km → ρ = {rho:.3e} kg/m³")

    # Test sigma inflation
    print("\n[3] Sigma Inflation (Kp=3, F10.7=150):")
    for alt in [200, 250, 300, 350, 400, 450, 500]:
        mult = drag_sigma_inflation(alt)
        print(f"  {alt:4d} km → {mult:.2f}x")

    # Test orbital velocity
    print("\n[4] Orbital Velocity:")
    for alt in [200, 300, 400, 500]:
        v = orbital_velocity(alt)
        print(f"  {alt:4d} km → {v:.0f} m/s ({v/1000:.2f} km/s)")

    # Test drag ΔV per orbit
    print("\n[5] Drag ΔV per orbit (B=50 kg/m²):")
    for alt in [200, 250, 300, 350, 400, 450, 500]:
        dv = drag_delta_v_per_orbit(alt)
        print(f"  {alt:4d} km → {dv:.4f} m/s/orbit")

    # Test orbital lifetime
    print("\n[6] Orbital Lifetime Estimate (B=50 kg/m², F10.7=150):")
    for alt in [200, 250, 300, 350, 400, 500]:
        days = estimate_orbital_lifetime(alt)
        if days > 365:
            print(f"  {alt:4d} km → {days/365:.1f} years")
        else:
            print(f"  {alt:4d} km → {days:.0f} days")

    # Test propagation
    print("\n[7] Drag-Aware Propagation (300km circular, 1 orbit):")
    alt0 = 300.0
    r0 = (EARTH_RADIUS_KM + alt0) * 1000.0
    v0 = orbital_velocity(alt0)
    T = orbital_period(alt0)
    pos0 = (r0, 0.0, 0.0)
    vel0 = (0.0, v0, 0.0)

    pos1_nodrag, vel1_nodrag = propagate_with_drag(pos0, vel0, T, ballistic_coef=1e10)
    pos1_drag, vel1_drag = propagate_with_drag(pos0, vel0, T, ballistic_coef=50.0)

    alt_nodrag = altitude_from_state(pos1_nodrag)
    alt_drag = altitude_from_state(pos1_drag)
    print(f"  Initial altitude:    {alt0:.1f} km")
    print(f"  After 1 orbit (no drag): {alt_nodrag:.2f} km")
    print(f"  After 1 orbit (B=50):    {alt_drag:.2f} km")
    print(f"  Altitude drop:       {alt0 - alt_drag:.3f} km")

    # Test VLEO conjunction assessment
    print("\n[8] VLEO Conjunction Assessment (300km, miss=500m, Pc=1e-3):")
    assess = vleo_conjunction_assessment(300, 500, 1e-3)
    for k, v in assess.items():
        print(f"  {k}: {v}")

    print("\n" + "=" * 60)
    print("  All tests completed.")
    print("=" * 60)
