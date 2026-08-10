"""Mission Design — collision-aware orbit comparison (Sprint #10).

DECISION SUPPORT, NOT autonomous design. Given candidate orbits (altitude/inc),
CAS compares each on multiple real-data dimensions so the operator can pick an
orbit informed by the conjunction environment — the design-phase analogue of
Launch Screening (Sprint #9, which screened a *given* orbit).

Inspired by NASA's Feb-2026 design-phase collision-risk tooling: integrate
conjunction risk into orbit selection *before* launch, not as a late reactive
assessment. CAS does the decision-support slice of that with data it actually
has — no fabricated breakup/fragmentation modelling.

FOUR DATA-BACKED DIMENSIONS (all from real CAS data, no invention):
  1. Catalog density        — objects per altitude band (.spacetrack cache,
                              type-separated: debris / rocket_body / payload).
  2. Debris fraction        — % of band that is non-maneuverable debris/RB
                              (a maneuverable-payload-heavy band is far safer
                              than a debris-heavy one at equal count).
  3. Historical conjunction — empirical conjunction frequency in the band from
     density                  conjunction_events (NORAD->altitude matched, 96%),
                              plus the debris-involved fraction of those.
  4. Orbital lifetime /      — vleo.estimate_orbital_lifetime + detect_regime:
     regime                   the trade-off axis (low alt = short life / less
                              accumulation but station-keeping burden; high alt
                              = long life but debris accumulates).

The output is MULTI-DIMENSIONAL on purpose: no single collapsed "risk = 7.3"
score (that would hide the trade-offs an operator must weigh themselves). We
present each dimension + a qualitative congestion label and let the operator
decide based on their own priorities (mission lifetime vs debris exposure).

IS / IS-NOT honesty:
  IS  : data-backed relative comparison of candidate orbits across catalog
        density, debris fraction, empirical conjunction history, and lifetime.
  ISNOT: collision-probability prediction for a specific spacecraft (needs the
        actual satellite + covariance — impossible at design phase); breakup/
        fragmentation emission modelling (needs NASA SBM — not in CAS);
        resolution/mass/altitude payload trade (mission engineering, not CAS).
        This is a CONGESTION/EXPOSURE proxy, not a collision forecast.
"""
import json
import math
import logging
import sys
from typing import Any, Dict, List, Optional

# vleo lives at /opt/cas (engine-level), not in cas_api/services
sys.path.insert(0, "/opt/cas")
import vleo  # estimate_orbital_lifetime, detect_regime

from core.database import get_dict_cursor

log = logging.getLogger(__name__)

MU_EARTH = 398600.4418   # km^3/s^2
R_EARTH = 6378.137       # km
_CACHE_FILE = "/opt/cas/.spacetrack_catalog_cache.json"

# In-process memo (cache file is refreshed by engine cron; we reload if stale).
_CATALOG_MEMO: Dict[str, Any] = {"fetched_at": None, "objects": None}


def _alt_from_tle_l2(l2: str) -> Optional[float]:
    """Mean altitude (km) from TLE line 2 mean motion (circular approximation).
    Columns 53-63 of l2 hold mean motion in revs/day (CCSDS/NORAD TLE format)."""
    try:
        mm = float(l2[52:63])
        if mm <= 0:
            return None
        n = mm * 2.0 * math.pi / 86400.0   # rad/s
        a = (MU_EARTH / (n * n)) ** (1.0 / 3.0)
        return a - R_EARTH
    except Exception:
        return None


def _inc_from_tle_l2(l2: str) -> Optional[float]:
    """Inclination (deg) from TLE line 2 (columns 9-16)."""
    try:
        return float(l2[8:16])
    except Exception:
        return None


_CATALOG_SOURCE = "spacetrack_cache"

_DB_TYPE_MAP = {"DEBRIS": "debris", "ROCKET BODY": "rocket_body", "PAYLOAD": "payload"}


def catalog_source() -> str:
    """Which source backed the most recent catalogue load."""
    return _CATALOG_SOURCE


def _load_catalog_from_db() -> List[Dict[str, Any]]:
    """Rebuild the LEO catalogue from satcat_objects (fallback path)."""
    sql = ("SELECT norad, object_type, apogee_km, perigee_km, inclination "
           "FROM satcat_objects "
           "WHERE decay_date IS NULL AND apogee_km IS NOT NULL "
           "AND perigee_km IS NOT NULL AND inclination IS NOT NULL "
           "AND object_type IN ('DEBRIS','ROCKET BODY','PAYLOAD')")
    rows = []
    try:
        from core.database import get_dict_cursor
        with get_dict_cursor() as cur:
            cur.execute(sql)
            rows = cur.fetchall()
    except Exception:
        try:
            import os as _os, psycopg2, psycopg2.extras
            conn = psycopg2.connect(_os.environ["DB_URL"])
            cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cur.execute(sql)
            rows = cur.fetchall()
            cur.close(); conn.close()
        except Exception as e:
            log.error("mission_design: DB fallback failed: %s", e)
            return []

    out: List[Dict[str, Any]] = []
    for r in rows:
        alt = (float(r["apogee_km"]) + float(r["perigee_km"])) / 2.0
        if not (100.0 < alt < 2000.0):
            continue
        out.append({
            "norad": str(r["norad"]).strip(),
            "type": _DB_TYPE_MAP.get(r["object_type"], "payload"),
            "alt": alt,
            "inc": float(r["inclination"]) if r["inclination"] is not None else None,
        })
    return out


def _load_catalog() -> List[Dict[str, Any]]:
    """Load type-separated catalog from the Space-Track cache, computing each
    object's altitude + inclination from its TLE. Memoized; reloads when the
    cache file's fetched_at changes (engine refreshes it on a cron)."""
    try:
        with open(_CACHE_FILE) as f:
            cache = json.load(f)
    except Exception as e:
        log.warning("mission_design: catalog cache load failed: %s", e)
        return _CATALOG_MEMO.get("objects") or []

    fetched = cache.get("fetched_at")
    if _CATALOG_MEMO["fetched_at"] == fetched and _CATALOG_MEMO["objects"] is not None:
        return _CATALOG_MEMO["objects"]

    objects: List[Dict[str, Any]] = []
    for typ in ("debris", "rocket_body", "payload"):
        for obj in cache.get(typ, []):
            l2 = obj.get("l2", "")
            alt = _alt_from_tle_l2(l2)
            if alt is None or not (100.0 < alt < 2000.0):  # LEO band only
                continue
            objects.append({
                "norad": str(obj.get("norad", "")).strip(),
                "type": typ,
                "alt": alt,
                "inc": _inc_from_tle_l2(l2),
            })

    # Fallback: if the Space-Track cache is empty or unusable, rebuild the
    # catalogue from satcat_objects. The four fields we need (norad, type,
    # altitude, inclination) are already columns there, so the system keeps
    # working on the last known good data instead of silently reporting an
    # empty sky. UNKNOWN-type objects are excluded to match the cache path.
    global _CATALOG_SOURCE
    if len(objects) < 1000:
        db_objects = _load_catalog_from_db()
        if db_objects:
            log.warning("mission_design: cache had %d objects, using satcat_objects "
                        "fallback (%d objects)", len(objects), len(db_objects))
            objects = db_objects
            _CATALOG_SOURCE = "satcat_db_fallback"
        else:
            _CATALOG_SOURCE = "spacetrack_cache"
    else:
        _CATALOG_SOURCE = "spacetrack_cache"

    _CATALOG_MEMO["fetched_at"] = fetched
    _CATALOG_MEMO["objects"] = objects
    log.info("mission_design: catalog loaded (%d LEO objects)", len(objects))
    return objects


def _catalog_band_stats(catalog: List[Dict[str, Any]], center_km: float,
                        half_km: float, inc_deg: Optional[float],
                        inc_tol_deg: float) -> Dict[str, Any]:
    """Density + type split for an altitude band, optionally inclination-gated."""
    lo, hi = center_km - half_km, center_km + half_km
    deb = rb = pay = 0
    for c in catalog:
        if not (lo <= c["alt"] <= hi):
            continue
        if inc_deg is not None and c["inc"] is not None:
            # inclination crossing relevance: count objects within tolerance OR
            # at the supplementary inc (retrograde crossings also matter).
            di = abs(c["inc"] - inc_deg)
            if di > inc_tol_deg and abs(180.0 - c["inc"] - inc_deg) > inc_tol_deg:
                continue
        t = c["type"]
        if t == "debris":
            deb += 1
        elif t == "rocket_body":
            rb += 1
        else:
            pay += 1
    total = deb + rb + pay
    nonman = deb + rb  # non-maneuverable threat
    return {
        "total": total,
        "debris": deb,
        "rocket_body": rb,
        "payload": pay,
        "non_maneuverable": nonman,
        "debris_fraction_pct": round(100.0 * nonman / total, 1) if total else 0.0,
        "band_km": [round(lo, 1), round(hi, 1)],
        "volume_proxy_band_km": round(2 * half_km, 1),
        "density_per_km_alt": round(total / (2 * half_km), 1) if half_km else 0.0,
    }


def _historical_conjunctions(catalog: List[Dict[str, Any]], center_km: float,
                             half_km: float) -> Dict[str, Any]:
    """Empirical conjunction frequency in this altitude band from
    conjunction_events (NORAD -> altitude matched via the catalog), plus the
    debris-involved fraction. Read-only; safe aggregate (no NORAD list leaked)."""
    norad_alt = {c["norad"]: c["alt"] for c in catalog}
    lo, hi = center_km - half_km, center_km + half_km
    total = 0
    debris_involved = 0
    try:
        with get_dict_cursor() as cur:
            cur.execute("SELECT norad1, norad2, sat1, sat2 FROM conjunction_events")
            for r in cur.fetchall():
                a1 = norad_alt.get(str(r["norad1"]).strip())
                a2 = norad_alt.get(str(r["norad2"]).strip())
                alts = [a for a in (a1, a2) if a is not None]
                if not alts:
                    continue
                avg = sum(alts) / len(alts)
                if lo <= avg <= hi:
                    total += 1
                    s1 = (r.get("sat1") or "").upper()
                    s2 = (r.get("sat2") or "").upper()
                    if "DEB" in s1 or "DEB" in s2:
                        debris_involved += 1
    except Exception as e:
        log.warning("mission_design: historical conjunction query failed: %s", e)
        return {"count": None, "debris_involved_pct": None, "note": "history unavailable"}

    return {
        "count": total,
        "debris_involved_pct": round(100.0 * debris_involved / total, 1) if total else 0.0,
        "window_note": "all retained CDM history (NORAD->altitude matched)",
    }


def _congestion_label(density_total: int, debris_fraction_pct: float,
                      hist_count: Optional[int]) -> str:
    """Qualitative congestion label combining count, debris fraction, and
    empirical conjunction history. Deliberately coarse (LOW/MODERATE/HIGH/
    SEVERE) — NOT a precise score, to avoid implying collision-probability
    precision. Debris fraction is weighted heavily (non-maneuverable threat)."""
    score = 0
    # density tier
    if density_total >= 3000:
        score += 2
    elif density_total >= 1000:
        score += 1
    # debris fraction tier (weighted — a debris-heavy band is the real hazard)
    if debris_fraction_pct >= 60:
        score += 3
    elif debris_fraction_pct >= 25:
        score += 2
    elif debris_fraction_pct >= 10:
        score += 1
    # empirical conjunction history tier
    if hist_count is not None:
        if hist_count >= 2000:
            score += 3
        elif hist_count >= 800:
            score += 2
        elif hist_count >= 200:
            score += 1
    return ("SEVERE" if score >= 7 else
            "HIGH" if score >= 5 else
            "MODERATE" if score >= 3 else
            "LOW")


def compare_orbits(candidates: List[Dict[str, Any]],
                   band_half_km: float = 25.0,
                   inc_tol_deg: float = 5.0,
                   ballistic_coef: float = 50.0,
                   f107_flux: float = 150.0) -> Dict[str, Any]:
    """Compare candidate orbits across four data-backed dimensions.

    candidates: list of {"altitude_km": float, "inclination_deg": float|None,
                         "label": str|None}
    Returns a per-candidate multi-dimensional comparison + qualitative label.
    No single collapsed score (operator weighs trade-offs themselves)."""
    catalog = _load_catalog()
    catalog_epoch = _CATALOG_MEMO.get("fetched_at")

    results = []
    for i, cand in enumerate(candidates):
        alt = float(cand["altitude_km"])
        inc = cand.get("inclination_deg")
        inc = float(inc) if inc is not None else None
        label = cand.get("label") or f"Candidate {i+1}"

        # Dimension 1+2: catalog density + debris fraction (inc-gated if given)
        density = _catalog_band_stats(catalog, alt, band_half_km, inc, inc_tol_deg)

        # Dimension 3: empirical conjunction history (altitude band, inc-agnostic
        # because conjunction geometry already encodes crossing relevance)
        history = _historical_conjunctions(catalog, alt, band_half_km)

        # Dimension 4: orbital lifetime + regime (the trade-off axis)
        try:
            lifetime_days = vleo.estimate_orbital_lifetime(
                alt, ballistic_coef=ballistic_coef, f107_flux=f107_flux)
            regime = vleo.detect_regime(alt)
        except Exception as e:
            log.warning("mission_design: lifetime calc failed @ %skm: %s", alt, e)
            lifetime_days, regime = None, None

        congestion = _congestion_label(
            density["total"], density["debris_fraction_pct"], history.get("count"))

        results.append({
            "label": label,
            "altitude_km": round(alt, 1),
            "inclination_deg": inc,
            "congestion_label": congestion,
            "catalog_density": density,
            "conjunction_history": history,
            "lifetime": {
                "estimated_days": round(lifetime_days, 1) if lifetime_days is not None else None,
                "estimated_years": round(lifetime_days / 365.25, 2) if lifetime_days is not None else None,
                "regime": regime,
                "note": ("VLEO — continuous station-keeping; debris clears fast"
                         if regime in ("vleo", "hybrid") else
                         "LEO — debris accumulates; longer mission life"),
            },
        })

    return {
        "candidates": results,
        "comparison_basis": {
            "catalog_epoch_unix": catalog_epoch,
            "catalog_objects_leo": len(catalog),
            "band_half_km": band_half_km,
            "inclination_tolerance_deg": inc_tol_deg,
            "lifetime_assumptions": {
                "ballistic_coefficient": ballistic_coef,
                "f107_flux": f107_flux,
                "note": "lifetime is sensitive to ballistic coef + solar activity; "
                        "values are order-of-magnitude design guidance, not predictions",
            },
        },
        "is": ["data-backed relative comparison across density, debris fraction, "
               "empirical conjunction history, and orbital lifetime"],
        "is_not": ["collision-probability prediction for a specific spacecraft",
                   "fragmentation/breakup modelling",
                   "a single collapsed risk score (trade-offs shown separately)"],
        "interpretation": "Congestion/exposure proxy for orbit selection. A "
                          "high-count band of maneuverable payloads (low debris "
                          "fraction) is generally safer than a lower-count band "
                          "dominated by debris. Weigh lifetime against exposure "
                          "per your mission priorities.",
    }
