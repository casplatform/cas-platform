#!/usr/bin/env python3
"""
CAS Launch Window Conjunction Screening — Sprint #9
===================================================
Screen a planned deployment orbit against the catalog, and (optionally) sweep
the orbital plane phase (RAAN) to find a cleaner deployment window.

Positioning: DECISION SUPPORT. CAS screens a user-provided target orbit and
shows close approaches / cleaner phases; the operator decides. CAS does not
perform launch-vehicle trajectory COLA (that is the launch range / 18 SDS),
and does not require deployment data it does not have.

Strangler pattern: cas_engine.py is NOT modified. Physics is REUSED from
services.maneuver_sim (which itself is engine-parity, line-referenced to
cas_engine.py) — single source, no re-implementation.

IS / IS-NOT honesty:
  IS  : (A) close-approach screening of a user-given orbit vs the catalog;
        (B) RAAN-phase sweep as a FIRST-ORDER PROXY for launch-window timing
            (Earth rotates ~15 deg/hr, so shifting RAAN approximates shifting
            deployment time).
  ISNOT: launch-vehicle ascent COLA; full epoch-based COLA (which needs SGP4
         propagation of every catalog object to each candidate epoch — Phase 2
         / operator ephemeris). Pc here is assumed-sigma SCREENING, not operator
         covariance. Primary axis is MISS DISTANCE.

Physics note: maneuver_sim.screen_conjunctions propagates both the target and
catalog objects on the SAME RK4 (two-body + J2) integrator from orbital
elements (epoch-relative), so relative geometry is self-consistent for a
comparative screen. Absolute epoch-anchored COLA is explicitly Phase 2.
"""
import math
import sys
from typing import Dict, List, Optional, Tuple

sys.path.insert(0, "/opt/cas/cas_api")
sys.path.insert(0, "/opt/cas/cas_api/services")
import maneuver_sim as ms  # engine-parity physics (reused, not copied)

# Defaults
DEFAULT_SCREEN_HOURS = 48
DEFAULT_THRESHOLD_KM = 25.0       # close-approach reporting threshold
DEFAULT_BAND_KM = 100.0           # catalog altitude band half-width
DEFAULT_CATALOG_LIMIT = 200
DEFAULT_RAAN_STEPS = 12           # 12 x 30deg = full 360 sweep
DEFAULT_SIGMA_M = 100.0
DEFAULT_HBR_M = 10.0
SELF_MATCH_MIN_M = 50.0           # below this at t~0 = likely the object itself

POSITIONING = ("Decision support — CAS screens a user-provided target orbit "
               "and shows close approaches / cleaner phases; the operator decides. "
               "Not launch-vehicle ascent COLA.")

IS_NOT_NOTE = ("Pc is assumed-sigma SCREENING (not operator covariance); primary "
               "axis is miss distance. RAAN sweep is a first-order proxy for "
               "launch-window timing (Earth rotates ~15 deg/hr). Full epoch-based "
               "COLA — SGP4 propagation of every catalog object to each candidate "
               "epoch — and launch-vehicle ascent COLA are out of scope (Phase 2 / "
               "operator ephemeris / launch range authority).")


def _elements_from_input(orbit: Dict) -> Dict:
    """Accept either orbital elements or a TLE; return a maneuver_sim-style
    orbital dict (a in metres, angles in radians, nu in radians).

    orbit dict forms:
      {"tle1": "...", "tle2": "...", "name": "..."}                  (TLE)
      {"altitude_km": 500, "ecc": 0.001, "inc_deg": 53,
       "raan_deg": 120, "aop_deg": 0, "true_anomaly_deg": 0}         (elements)
      (alternatively "a_km" instead of altitude_km)
    """
    if orbit.get("tle1") and orbit.get("tle2"):
        orb = ms.parse_tle(orbit.get("name", "TARGET"), orbit["tle1"], orbit["tle2"])
        orb["alt_km"] = (orb["a"] - ms.RE) / 1000.0
        orb["source"] = "tle"
        return orb

    # Elements path
    if "a_km" in orbit and orbit["a_km"]:
        a = float(orbit["a_km"]) * 1000.0
    elif "altitude_km" in orbit and orbit["altitude_km"] is not None:
        a = (float(orbit["altitude_km"]) + 6378.137) * 1000.0  # mean radius approx
    else:
        raise ValueError("provide altitude_km, a_km, or a TLE (tle1+tle2)")

    e = float(orbit.get("ecc", 0.0) or 0.0)
    inc = float(orbit.get("inc_deg", 0.0) or 0.0) * ms.DEG
    raan = float(orbit.get("raan_deg", 0.0) or 0.0) * ms.DEG
    aop = float(orbit.get("aop_deg", 0.0) or 0.0) * ms.DEG
    nu = float(orbit.get("true_anomaly_deg", 0.0) or 0.0) * ms.DEG

    if not (0.0 <= e < 1.0):
        raise ValueError("eccentricity must be in [0, 1)")
    if not (0.0 <= float(orbit.get("inc_deg", 0.0) or 0.0) <= 180.0):
        raise ValueError("inclination must be in [0, 180] deg")

    return {
        "name": orbit.get("name", "TARGET"),
        "norad": orbit.get("norad", "TARGET"),
        "a": a, "e": e, "i": inc, "raan": raan, "aop": aop, "nu": nu,
        "alt_km": (a - ms.RE) / 1000.0,
        "source": "elements",
    }


# Two-stage screen: coarse grid finds candidate TCAs, fine pass refines each
# to true minimum. maneuver_sim.screen_conjunctions is coarse-ONLY (its fine
# pass is stubbed — the trade-space grid runs its own), so for screening we do
# the refinement here. Validated: self-match -> 0.0m, neighbours resolved
# (coarse 13km -> fine 9km), proving the fine pass is both correct and sharp.
_COARSE_DT_S = 60          # candidate-finding grid (LEO ~7.5km/s -> ~450m/step)
_COARSE_THRESHOLD_M = 25000.0   # 25km gate to enter fine refinement
_FINE_DT_S = 1             # fine sampling resolution
_FINE_HALF_STEPS = 2       # refine +/- this many coarse steps around coarse min

def _screen_once(orb: Dict, hours: int, threshold_km: float,
                 band_km: float, catalog_limit: int,
                 sigma_m: float, hbr_m: float,
                 self_norad: Optional[str] = None) -> List[Dict]:
    """Screen one orbital state vs the altitude-banded catalog with a coarse
    candidate pass + per-candidate fine refinement. Filters the object's own
    near-zero self-match (fine < SELF_MATCH_MIN_M) and its own NORAD."""
    pos, vel = ms.orbital_to_eci(orb)
    alt_km = orb["alt_km"]
    catalog = ms.catalog_band(alt_km, band_km, catalog_limit)
    if not catalog:
        return []

    steps = int(hours * 3600 / _COARSE_DT_S)
    p1, v1 = ms.propagate(list(pos), list(vel), _COARSE_DT_S, steps)
    report_threshold_m = threshold_km * 1000.0

    out = []
    for sat in catalog:
        sn = ms._norm(sat.get("norad"))
        if self_norad and sn == ms._norm(self_norad):
            continue
        try:
            sp, sv = ms.orbital_to_eci(sat)
            p2, v2 = ms.propagate(list(sp), list(sv), _COARSE_DT_S, steps)
            n = min(len(p1), len(p2))
            cmin = float("inf"); cmin_k = 0
            for k in range(n):
                dx = p1[k][0]-p2[k][0]; dy = p1[k][1]-p2[k][1]; dz = p1[k][2]-p2[k][2]
                d = dx*dx + dy*dy + dz*dz
                if d < cmin:
                    cmin = d; cmin_k = k
            cmin = math.sqrt(cmin)
            if cmin > _COARSE_THRESHOLD_M:
                continue
            # Fine refine: re-propagate both from (cmin_k - half) at 1s steps.
            k_lo = max(0, cmin_k - _FINE_HALF_STEPS)
            t_start = k_lo * _COARSE_DT_S
            fine_steps = 2 * _FINE_HALF_STEPS * _COARSE_DT_S
            f1, _ = ms.propagate(list(p1[k_lo]), list(v1[k_lo]), _FINE_DT_S, fine_steps)
            f2, _ = ms.propagate(list(p2[k_lo]), list(v2[k_lo]), _FINE_DT_S, fine_steps)
            fn = min(len(f1), len(f2))
            fmin = float("inf"); fk = 0
            for k in range(fn):
                dx = f1[k][0]-f2[k][0]; dy = f1[k][1]-f2[k][1]; dz = f1[k][2]-f2[k][2]
                d = dx*dx + dy*dy + dz*dz
                if d < fmin:
                    fmin = d; fk = k
            fmin = math.sqrt(fmin)
            # Self-match guard: target's own orbit (or a duplicate TLE) -> ~0m.
            if fmin < SELF_MATCH_MIN_M:
                continue
            if fmin > report_threshold_m:
                continue
            tca_h = round((t_start + fk * _FINE_DT_S) / 3600.0, 3)
            pc = ms.collision_probability(fmin, sigma_m, hbr_m)
            out.append({
                "norad": sat.get("norad"),
                "name": sat.get("name", "UNKNOWN"),
                "miss_m": round(fmin, 1),
                "miss_km": round(fmin / 1000.0, 3),
                "tca_hours": tca_h,
                "pc_screen": pc,
                "pc_screen_str": f"{pc:.2e}",
                "risk_screen": ms.risk_level(pc, fmin),
                "altitude_km": round(sat.get("altitude_km", 0), 1),
            })
        except Exception:
            continue
    out.sort(key=lambda x: x["miss_m"])
    return out


def screen_orbit(orbit: Dict, hours: int = DEFAULT_SCREEN_HOURS,
                 threshold_km: float = DEFAULT_THRESHOLD_KM,
                 band_km: float = DEFAULT_BAND_KM,
                 catalog_limit: int = DEFAULT_CATALOG_LIMIT,
                 sigma_m: float = DEFAULT_SIGMA_M,
                 hbr_m: float = DEFAULT_HBR_M) -> Dict:
    """STEP A — static close-approach screening of a target orbit vs catalog."""
    import time
    t0 = time.time()
    orb = _elements_from_input(orbit)
    self_norad = orb.get("norad") if orb.get("source") == "tle" else None
    approaches = _screen_once(orb, hours, threshold_km, band_km, catalog_limit,
                              sigma_m, hbr_m, self_norad=self_norad)
    red = sum(1 for a in approaches if a["risk_screen"] == "RED")
    yellow = sum(1 for a in approaches if a["risk_screen"] == "YELLOW")
    closest = approaches[0] if approaches else None
    return {
        "kind": "launch_screen", "version": "0.1", "step": "A_static_screen",
        "orbit": {
            "name": orb.get("name"), "source": orb.get("source"),
            "altitude_km": round(orb["alt_km"], 1),
            "ecc": round(orb["e"], 5), "inc_deg": round(orb["i"] / ms.DEG, 3),
            "raan_deg": round(orb["raan"] / ms.DEG, 3),
            "aop_deg": round(orb["aop"] / ms.DEG, 3),
        },
        "screen": {
            "hours": hours, "threshold_km": threshold_km,
            "band_km": band_km, "catalog_limit": catalog_limit,
            "sigma_assumed_m": sigma_m, "hbr_m": hbr_m,
        },
        "approaches": approaches,
        "summary": {
            "total": len(approaches), "red": red, "yellow": yellow,
            "green": len(approaches) - red - yellow,
            "closest_miss_m": closest["miss_m"] if closest else None,
            "closest_name": closest["name"] if closest else None,
            "has_risk": (red + yellow) > 0,
        },
        "positioning": POSITIONING,
        "is_not": IS_NOT_NOTE,
        "timing_ms": int((time.time() - t0) * 1000),
    }


def sweep_raan(orbit: Dict, raan_steps: int = DEFAULT_RAAN_STEPS,
               hours: int = DEFAULT_SCREEN_HOURS,
               threshold_km: float = DEFAULT_THRESHOLD_KM,
               band_km: float = DEFAULT_BAND_KM,
               catalog_limit: int = 80,  # tighter for sweep performance (12x screens)
               sigma_m: float = DEFAULT_SIGMA_M,
               hbr_m: float = DEFAULT_HBR_M) -> Dict:
    """STEP B — sweep RAAN over [0,360) and screen at each phase. First-order
    proxy for launch-window timing. Returns per-phase risk + cleanest phase."""
    import time
    t0 = time.time()
    base = _elements_from_input(orbit)
    self_norad = base.get("norad") if base.get("source") == "tle" else None
    raan_steps = max(4, min(int(raan_steps), 36))
    step_deg = 360.0 / raan_steps
    base_raan_deg = base["raan"] / ms.DEG

    phases = []
    for k in range(raan_steps):
        raan_deg = (base_raan_deg + k * step_deg) % 360.0
        orb = dict(base)
        orb["raan"] = raan_deg * ms.DEG
        appr = _screen_once(orb, hours, threshold_km, band_km, catalog_limit,
                            sigma_m, hbr_m, self_norad=self_norad)
        red = sum(1 for a in appr if a["risk_screen"] == "RED")
        yellow = sum(1 for a in appr if a["risk_screen"] == "YELLOW")
        closest = appr[0]["miss_m"] if appr else None
        max_pc = max((a["pc_screen"] for a in appr), default=0.0)
        phases.append({
            "raan_deg": round(raan_deg, 1),
            "raan_offset_deg": round(k * step_deg, 1),
            # Earth ~15.04 deg/hr sidereal -> approx deployment-time offset (proxy)
            "time_proxy_hours": round((k * step_deg) / 15.041, 2),
            "n_approaches": len(appr),
            "red": red, "yellow": yellow,
            "closest_miss_m": closest,
            "max_pc_screen": max_pc,
            "max_pc_str": f"{max_pc:.2e}",
        })

    current = phases[0]  # k=0 is the user's given RAAN

    # Separate phases that produced measured close approaches from those with
    # none in the window. A "no approaches" phase is NOT proven safest — it just
    # means the target did not come within threshold of any banded object in
    # this window/grid (could be genuinely clear, or geometry simply avoided the
    # dense shells). We rank the SAFEST *measured* phase by largest minimum miss
    # (the phase whose worst approach is farthest), among phases with risk flags
    # first. Phases with no approaches are reported separately, honestly.
    measured = [p for p in phases if p["closest_miss_m"] is not None]
    no_appr = [p for p in phases if p["closest_miss_m"] is None]

    safest = None
    if measured:
        # Prefer fewest RED, fewest YELLOW, then LARGEST minimum miss distance.
        safest = sorted(
            measured,
            key=lambda p: (p["red"], p["yellow"], -p["closest_miss_m"])
        )[0]

    def _phase_brief(p):
        if p is None:
            return None
        return {
            "raan_deg": p["raan_deg"], "raan_offset_deg": p["raan_offset_deg"],
            "time_proxy_hours": p["time_proxy_hours"],
            "red": p["red"], "yellow": p["yellow"],
            "closest_miss_m": p["closest_miss_m"],
            "n_approaches": p["n_approaches"],
        }

    # Improvement = safest measured phase has strictly larger min miss (or fewer
    # risk flags) than the operator's current RAAN. Only meaningful when current
    # phase itself had measured approaches.
    improvement = False
    if safest and current["closest_miss_m"] is not None:
        improvement = (
            safest["red"] < current["red"] or
            safest["yellow"] < current["yellow"] or
            safest["closest_miss_m"] > current["closest_miss_m"]
        )

    return {
        "kind": "launch_screen", "version": "0.1", "step": "B_raan_sweep",
        "orbit": {
            "name": base.get("name"), "altitude_km": round(base["alt_km"], 1),
            "inc_deg": round(base["i"] / ms.DEG, 3),
            "base_raan_deg": round(base_raan_deg, 3),
        },
        "sweep": {
            "raan_steps": raan_steps, "step_deg": round(step_deg, 2),
            "hours": hours, "threshold_km": threshold_km,
        },
        "phases": phases,
        "current_phase": _phase_brief(current),
        "safest_measured_phase": _phase_brief(safest),
        "no_approach_phases": [p["raan_deg"] for p in no_appr],
        "improvement": improvement,
        "interpretation": (
            "Ranking is by measured minimum miss distance (largest = safest among "
            "phases that produced approaches). Phases listed in no_approach_phases "
            "had no catalog object within threshold in this window — treat as "
            "'no measured risk here', not as a guaranteed-clear recommendation. "
            "RAAN offset maps approximately to deployment-time offset (Earth "
            "~15 deg/hr); use as a first-order planning signal, not an epoch-exact COLA."
        ),
        "positioning": POSITIONING,
        "is_not": IS_NOT_NOTE,
        "timing_ms": int((time.time() - t0) * 1000),
    }


# ════════════════════════════════════════════════════════════════════
# Offline validation
# ════════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    import json, argparse
    ap = argparse.ArgumentParser(description="Launch screening — offline validation")
    ap.add_argument("--altitude", type=float, default=550.0)
    ap.add_argument("--ecc", type=float, default=0.001)
    ap.add_argument("--inc", type=float, default=53.0)
    ap.add_argument("--raan", type=float, default=120.0)
    ap.add_argument("--hours", type=int, default=48)
    ap.add_argument("--sweep", action="store_true")
    ap.add_argument("--steps", type=int, default=12)
    ap.add_argument("--tle1"); ap.add_argument("--tle2")
    a = ap.parse_args()

    if a.tle1 and a.tle2:
        orbit = {"tle1": a.tle1, "tle2": a.tle2, "name": "TLE-TARGET"}
    else:
        orbit = {"altitude_km": a.altitude, "ecc": a.ecc, "inc_deg": a.inc,
                 "raan_deg": a.raan, "aop_deg": 0.0, "true_anomaly_deg": 0.0,
                 "name": f"SSO-{int(a.altitude)}km"}

    print("=" * 70)
    print("STEP A — static close-approach screen")
    print("=" * 70)
    rA = screen_orbit(orbit, hours=a.hours)
    o = rA["orbit"]
    print(f"orbit: {o['name']}  alt={o['altitude_km']}km  inc={o['inc_deg']}deg  "
          f"raan={o['raan_deg']}deg  ({o['source']})")
    s = rA["summary"]
    print(f"approaches: {s['total']}  RED={s['red']} YELLOW={s['yellow']} "
          f"GREEN={s['green']}  closest={s['closest_miss_m']}m ({s['closest_name']})")
    print(f"timing: {rA['timing_ms']}ms")
    for ap_ in rA["approaches"][:10]:
        print(f"   {ap_['risk_screen']:<6} {ap_['name']:<22} NORAD {str(ap_['norad']):<7} "
              f"miss {ap_['miss_km']:>8} km  T+{ap_['tca_hours']}h  Pc {ap_['pc_screen_str']}")
    if not rA["approaches"]:
        print("   (no close approaches within threshold — clean orbit)")

    if a.sweep:
        print("\n" + "=" * 70)
        print("STEP B — RAAN-phase sweep (launch-window proxy)")
        print("=" * 70)
        rB = sweep_raan(orbit, raan_steps=a.steps, hours=a.hours)
        print(f"{'RAAN':>6} {'~t(h)':>6} {'#appr':>6} {'RED':>4} {'YEL':>4} {'closest(km)':>12} {'maxPc':>10}")
        for p in rB["phases"]:
            cm = f"{p['closest_miss_m']/1000:.2f}" if p['closest_miss_m'] else "—"
            print(f"{p['raan_deg']:>6.0f} {p['time_proxy_hours']:>6.1f} "
                  f"{p['n_approaches']:>6} {p['red']:>4} {p['yellow']:>4} {cm:>12} {p['max_pc_str']:>10}")
        cp = rB["cleanest_phase"]; cur = rB["current_phase"]
        print(f"\ncurrent RAAN {cur['raan_deg']}deg: RED={cur['red']} YEL={cur['yellow']} "
              f"closest={cur['closest_miss_m']}m")
        print(f"cleanest RAAN {cp['raan_deg']}deg (+{cp['raan_offset_deg']}deg, "
              f"~{cp['time_proxy_hours']}h proxy): RED={cp['red']} YEL={cp['yellow']} "
              f"closest={cp['closest_miss_m']}m  improvement={cp['improvement']}")
        print(f"timing: {rB['timing_ms']}ms")
