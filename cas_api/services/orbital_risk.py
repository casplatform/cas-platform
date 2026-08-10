"""Orbital Risk Assessment — insurance-facing exposure characterisation.

DECISION SUPPORT, NOT an actuarial model. Produces the *orbital exposure* of a
given shell (altitude ± band, optionally inclination-gated):

  1. Catalogue state    — threat population (debris + rocket bodies) vs active
  2. Kinetic burden     — rho_threat * v_rel * A_ref  [collisions/yr per ref. area]
  3. Environment trend  — how the threat population evolved (tle_history archive)
  4. Cascade exposure   — objects x debris-cloud persistence (NASA SBM + drag)
  5. Debris flux        — RESERVED for ESA MASTER-8 (lethal non-trackable 1-10cm)

IS      : catalogue-derived exposure of an orbital shell, with an explicit trend.
IS NOT  : satellite-specific collision probability (public catalogues carry no
          covariance), a premium, or an actuarial rate. The underwriter adds
          insured value, launch vehicle record and operator history.

Reuses the validated engine helpers via mission_design (import-only).
"""
import logging
import math
from typing import Any, Dict, List, Optional

from core.database import get_dict_cursor
from services import mission_design as md

try:
    import vleo  # cascade persistence (NASA SBM + drag)
except Exception:  # pragma: no cover
    vleo = None

log = logging.getLogger(__name__)

RE_KM = 6378.137
MU = 398600.4418
A_REF_KM2 = 1e-5          # 10 m^2 reference cross-section
SEC_PER_YEAR = 3.15576e7
TREND_FIRST_YEAR = 2020
TREND_LAST_YEAR = 2026

# NORAD -> type, memoised from the live catalogue cache
_TYPE_MEMO: Dict[str, Any] = {"map": None, "threat": None}


def _type_map() -> Dict[int, str]:
    """NORAD -> object type from the live Space-Track cache. Object type does
    not change over time, so today's classification is valid historically."""
    if _TYPE_MEMO["map"] is not None:
        return _TYPE_MEMO["map"]
    tmap: Dict[int, str] = {}
    try:
        cat = md._load_catalog()
        for c in cat:
            n = str(c.get("norad", "")).strip()
            if n.isdigit():
                tmap[int(n)] = c.get("type", "unknown")
    except Exception as e:
        log.warning("orbital_risk: type map build failed: %s", e)
    _TYPE_MEMO["map"] = tmap
    _TYPE_MEMO["threat"] = {n for n, t in tmap.items()
                            if t in ("debris", "rocket_body")}
    return tmap


def _threat_set() -> set:
    if _TYPE_MEMO["threat"] is None:
        _type_map()
    return _TYPE_MEMO["threat"] or set()


def _shell_volume_km3(center_km: float, half_km: float) -> float:
    r_lo, r_hi = RE_KM + center_km - half_km, RE_KM + center_km + half_km
    return (4.0 / 3.0) * math.pi * (r_hi ** 3 - r_lo ** 3)


def _mean_relative_velocity(center_km: float) -> float:
    """Isotropic-encounter mean relative velocity ~ (4/pi) * v_circular."""
    v_c = math.sqrt(MU / (RE_KM + center_km))
    return (4.0 / math.pi) * v_c


def catalogue_state(altitude_km: float, inclination_deg: Optional[float],
                    band_half_km: float, inc_tol_deg: float) -> Dict[str, Any]:
    """Current shell population, split by manoeuvrability."""
    cat = md._load_catalog()
    st = md._catalog_band_stats(cat, altitude_km, band_half_km,
                                inclination_deg, inc_tol_deg)
    return {
        "total": st["total"],
        "threat": st["non_maneuverable"],          # debris + rocket bodies
        "debris": st["debris"],
        "rocket_body": st["rocket_body"],
        "payload": st["payload"],
        "threat_fraction_pct": st["debris_fraction_pct"],
        "band_km": st["band_km"],
        "inclination_gated": inclination_deg is not None,
    }


def kinetic_burden(altitude_km: float, threat_count: int,
                   band_half_km: float) -> Dict[str, Any]:
    """Lambda = rho_threat * v_rel * A_ref  -> collisions/year per 10 m^2."""
    vol = _shell_volume_km3(altitude_km, band_half_km)
    rho = (threat_count / vol) if vol > 0 else 0.0
    v_rel = _mean_relative_velocity(altitude_km)
    lam = rho * v_rel * A_REF_KM2 * SEC_PER_YEAR
    return {
        "rho_threat_per_km3": rho,
        "v_rel_mean_km_s": v_rel,
        "shell_volume_km3": vol,
        "lambda_per_year": lam,
        "years_between_expected": (1.0 / lam) if lam > 0 else None,
        "reference_area_m2": 10.0,
    }


def environment_trend(altitude_km: float, inclination_deg: Optional[float],
                      band_half_km: float, inc_tol_deg: float,
                      mode: str = "inclination") -> Dict[str, Any]:
    """Threat-population evolution from the tle_history archive (2020-2026).

    mode: 'inclination' (default, gated to crossing-relevant planes) or 'band'
          (whole altitude shell, all inclinations).
    """
    threat = _threat_set()
    gated = (mode == "inclination") and (inclination_deg is not None)
    series: List[Dict[str, Any]] = []
    try:
        with get_dict_cursor() as cur:
            for yr in range(TREND_FIRST_YEAR, TREND_LAST_YEAR + 1):
                q = ("SELECT DISTINCT norad FROM tle_history "
                     "WHERE alt_km BETWEEN %s AND %s "
                     "AND epoch BETWEEN %s AND %s")
                p = [altitude_km - band_half_km, altitude_km + band_half_km,
                     f"{yr}-06-01", f"{yr}-07-15"]
                if gated:
                    q += (" AND (abs(inc_deg - %s) <= %s "
                          "OR abs(180 - inc_deg - %s) <= %s)")
                    p += [inclination_deg, inc_tol_deg,
                          inclination_deg, inc_tol_deg]
                cur.execute(q, p)
                norads = {r["norad"] for r in cur.fetchall()}
                n_threat = len(norads & threat)
                vol = _shell_volume_km3(altitude_km, band_half_km)
                lam = ((n_threat / vol) * _mean_relative_velocity(altitude_km)
                       * A_REF_KM2 * SEC_PER_YEAR) if vol > 0 else 0.0
                series.append({"year": yr, "threat_objects": n_threat,
                               "lambda_per_year": lam})
    except Exception as e:
        log.warning("orbital_risk: trend query failed: %s", e)
        return {"available": False, "note": "archive unavailable"}

    if len(series) < 2 or series[0]["threat_objects"] == 0:
        last = series[-1]["threat_objects"] if series else 0
        return {"available": False, "series": series,
                "threat_last": last,
                "note": ("No threat objects in this shell at the start of the "
                         f"archive ({TREND_FIRST_YEAR}); a growth rate cannot be "
                         f"computed from a zero baseline. Current count: {last}.")}

    years = series[-1]["year"] - series[0]["year"]
    ratio = series[-1]["threat_objects"] / series[0]["threat_objects"]
    cagr = ((ratio ** (1.0 / years)) - 1.0) * 100.0 if years > 0 else 0.0
    return {
        "available": True,
        "mode": "inclination-gated" if gated else "band-based",
        "series": series,
        "first_year": series[0]["year"], "last_year": series[-1]["year"],
        "threat_first": series[0]["threat_objects"],
        "threat_last": series[-1]["threat_objects"],
        "cagr_pct_per_year": round(cagr, 1),
        "source": "CAS catalogue archive (weekly-sampled TLE history)",
    }


def _band_mass_profile(altitude_km: float, half_km: float = 50.0) -> Dict[str, Any]:
    """Representative fragmenting mass for the shell, from DISCOS where known.

    Fragment production scales with colliding mass, so a single fixed assumption
    misrepresents both ends: Envisat is 8.1 t, a cubesat 4 kg. We take the
    median mass of catalogued objects in the band that DISCOS knows about, and
    report the coverage so the figure can be judged.
    """
    lo, hi = altitude_km - half_km, altitude_km + half_km
    try:
        with get_dict_cursor() as cur:
            cur.execute("""
                SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY mass_kg) AS median_mass,
                       max(mass_kg) AS max_mass,
                       count(*) FILTER (WHERE mass_kg IS NOT NULL) AS with_mass,
                       count(*) AS total
                FROM satcat_objects
                WHERE decay_date IS NULL
                  AND apogee_km IS NOT NULL AND perigee_km IS NOT NULL
                  AND perigee_km <= %s AND apogee_km >= %s
            """, (hi, lo))
            r = cur.fetchone() or {}
            med = r.get("median_mass")
            return {
                "median_mass_kg": round(float(med), 1) if med else None,
                "max_mass_kg": round(float(r["max_mass"]), 1) if r.get("max_mass") else None,
                "objects_with_mass": r.get("with_mass") or 0,
                "objects_in_band": r.get("total") or 0,
                "source": "ESA DISCOS",
            }
    except Exception as e:
        log.warning("orbital_risk: mass profile failed: %s", e)
        return {"median_mass_kg": None, "objects_with_mass": 0, "source": None}


def cascade_exposure(altitude_km: float,
                     target_mass_kg: Optional[float] = None) -> Dict[str, Any]:
    """How many objects are exposed, and for how long, after a fragmentation."""
    cat = md._load_catalog()
    pool = md._catalog_band_stats(cat, altitude_km, 50.0, None, 5.0)["total"]
    profile = _band_mass_profile(altitude_km)
    # prefer the subject's own mass; otherwise the band median; otherwise the
    # library default, flagged as such
    mass = target_mass_kg or profile.get("median_mass_kg")
    mass_basis = ("subject" if target_mass_kg else
                  ("band median (DISCOS)" if profile.get("median_mass_kg")
                   else "model default"))
    clearing_days = None
    if vleo is not None:
        try:
            cz = (vleo.assess_vleo_cascade(altitude_km, target_mass_kg=float(mass))
                  if mass else vleo.assess_vleo_cascade(altitude_km))
            for k, v in (cz or {}).items():
                if isinstance(v, dict):
                    for kk, vv in v.items():
                        if "90" in str(kk) and isinstance(vv, (int, float)):
                            clearing_days = float(vv)
                elif "90" in str(k) and isinstance(v, (int, float)):
                    clearing_days = float(v)
            if clearing_days is None:
                clearing_days = vleo.estimate_orbital_lifetime(
                    altitude_km, ballistic_coef=50.0, f107_flux=150.0)
        except Exception as e:
            log.warning("orbital_risk: cascade calc failed: %s", e)
    return {
        "exposed_pool": pool,
        "band_half_km": 50.0,
        "cloud_clearing_days_90pct": clearing_days,
        "cloud_clearing_years_90pct": (clearing_days / 365.25) if clearing_days else None,
        "fragmenting_mass_kg": round(float(mass), 1) if mass else None,
        "mass_basis": mass_basis,
        "band_mass_profile": profile,
        "model": "NASA Standard Breakup Model + atmospheric drag",
    }


def debris_flux(altitude_km: float, inclination_deg: Optional[float]) -> Dict[str, Any]:
    """RESERVED — ESA MASTER-8 lethal non-trackable (1-10 cm) flux.

    Public catalogues only cover objects down to ~10 cm; the mission-terminating
    hazard from 1-10 cm debris requires a statistical model (ESA MASTER / NASA
    ORDEM). Pending licence clarification, this is reported as unavailable
    rather than estimated — we do not substitute an unvalidated proxy.
    """
    return {
        "available": False,
        "model": None,
        "note": ("Lethal non-trackable (1-10 cm) flux requires ESA MASTER-8 or "
                 "NASA ORDEM. Not included in this assessment."),
    }


def resolve_norad(norad: int) -> Optional[Dict[str, Any]]:
    """Look up a catalogued object's current orbit + identity.

    Returns None if the object is not in the live catalogue (deorbited,
    classified, or never tracked).
    """
    try:
        cat = md._load_catalog()
    except Exception as e:
        log.warning("orbital_risk: catalogue load failed: %s", e)
        return None
    hit = None
    for c in cat:
        try:
            if int(str(c.get("norad", "")).strip()) == int(norad):
                hit = c
                break
        except (ValueError, TypeError, KeyError):
            continue
    if hit is None:
        return None

    out = {
        "norad": int(norad),
        "name": f"NORAD {norad}",
        "altitude_km": round(float(hit["alt"]), 1),
        "inclination_deg": (round(float(hit["inc"]), 2)
                            if hit.get("inc") is not None else None),
        "object_type": hit.get("type", "unknown"),
    }
    # identity from the Space-Track SATCAT (name, size class, launch record)
    try:
        with get_dict_cursor() as cur:
            cur.execute("""SELECT name, object_type, rcs_size, rcs_value, country,
                                  launch_date, mass_kg, mass_source
                           FROM satcat_objects WHERE norad=%s""", (int(norad),))
            r = cur.fetchone()
            if r:
                if r.get("name"):
                    out["name"] = r["name"]
                out["rcs_size"] = r.get("rcs_size")
                out["rcs_value_m2"] = r.get("rcs_value")
                out["country"] = r.get("country")
                out["launch_date"] = (r["launch_date"].isoformat()
                                      if r.get("launch_date") else None)
                if r.get("mass_kg"):
                    out["mass_kg"] = r["mass_kg"]
                    out["mass_source"] = r.get("mass_source")
                if r.get("object_type"):
                    out["catalogue_type"] = r["object_type"]
    except Exception as e:
        log.warning("orbital_risk: satcat lookup failed for %s: %s", norad, e)
    return out


def assess_norad(norad: int,
                 band_half_km: float = 25.0,
                 inc_tol_deg: float = 5.0,
                 trend_mode: str = "inclination") -> Dict[str, Any]:
    """Assess the shell a catalogued object currently occupies.

    Note this characterises the *environment around* the object, not the object
    itself: the subject satellite is part of the population, but the figure
    reported is the exposure any 10 m^2 asset would face in that shell.
    """
    obj = resolve_norad(norad)
    if obj is None:
        raise ValueError(f"NORAD {norad} not found in the current public catalogue")
    a = assess_orbit(altitude_km=obj["altitude_km"],
                     inclination_deg=obj["inclination_deg"],
                     band_half_km=band_half_km,
                     inc_tol_deg=inc_tol_deg,
                     trend_mode=trend_mode)
    # recompute cascade with the subject's own mass when DISCOS knows it
    if obj.get("mass_kg"):
        try:
            a["cascade"] = cascade_exposure(obj["altitude_km"],
                                            target_mass_kg=float(obj["mass_kg"]))
        except Exception as e:
            log.warning("orbital_risk: subject-mass cascade failed: %s", e)
    a["subject"] = obj
    return a


# LEO percentile reference — memoised, rebuilt on catalogue reload
_PCTL_MEMO: Dict[str, Any] = {"grid": None, "epoch": None}


def _percentile_grid() -> List[float]:
    """Kinetic burden across the whole LEO shell range, for ranking.

    Sampled every 25 km from 300-1400 km with the same threat-based method,
    so a given orbit can be positioned against the population it competes with.
    """
    epoch = None
    try:
        epoch = md._CATALOG_MEMO.get("fetched_at")
    except Exception:
        pass
    if _PCTL_MEMO["grid"] is not None and _PCTL_MEMO["epoch"] == epoch:
        return _PCTL_MEMO["grid"]

    cat = md._load_catalog()
    grid: List[float] = []
    for alt in range(300, 1401, 25):
        st = md._catalog_band_stats(cat, float(alt), 25.0, None, 5.0)
        lam = kinetic_burden(float(alt), st["non_maneuverable"], 25.0)["lambda_per_year"]
        grid.append(lam)
    grid.sort()
    _PCTL_MEMO["grid"] = grid
    _PCTL_MEMO["epoch"] = epoch
    return grid


def leo_percentile(lambda_per_year: float) -> Dict[str, Any]:
    """Where this burden sits against the LEO shell population."""
    grid = _percentile_grid()
    if not grid:
        return {"available": False}
    below = sum(1 for x in grid if x <= lambda_per_year)
    pct = 100.0 * below / len(grid)
    return {
        "available": True,
        "percentile": int(round(pct)),
        "sample_shells": len(grid),
        "range_km": [300, 1400],
        "note": (f"Higher collision burden than {int(round(pct))}% of sampled "
                 "LEO shells (300-1400 km, 25 km steps, threat-based)."),
    }


def assess_orbit(altitude_km: float,
                 inclination_deg: Optional[float] = None,
                 band_half_km: float = 25.0,
                 inc_tol_deg: float = 5.0,
                 trend_mode: str = "inclination") -> Dict[str, Any]:
    """Full orbital exposure assessment for one orbit."""
    cat_state = catalogue_state(altitude_km, inclination_deg,
                                band_half_km, inc_tol_deg)
    burden = kinetic_burden(altitude_km, cat_state["threat"], band_half_km)
    trend_inc = environment_trend(altitude_km, inclination_deg, band_half_km,
                                  inc_tol_deg, mode="inclination")
    trend_band = environment_trend(altitude_km, None, band_half_km,
                                   inc_tol_deg, mode="band")
    cascade = cascade_exposure(altitude_km)
    flux = debris_flux(altitude_km, inclination_deg)

    pctl = leo_percentile(burden["lambda_per_year"])

    return {
        "percentile": pctl,
        "orbit": {
            "altitude_km": round(altitude_km, 1),
            "inclination_deg": inclination_deg,
            "band_half_km": band_half_km,
        },
        "catalogue": cat_state,
        "burden": burden,
        "trend": {
            "primary": trend_inc if trend_mode == "inclination" else trend_band,
            "inclination_gated": trend_inc,
            "band_based": trend_band,
        },
        "cascade": cascade,
        "debris_flux": flux,
        "boundaries": {
            "is": ("Catalogue-derived orbital exposure of the shell, with an "
                   "explicit multi-year trend."),
            "is_not": ("Not a satellite-specific collision probability, not a "
                       "premium, not an actuarial rate. Public catalogues carry "
                       "no covariance; insured value, launch vehicle record and "
                       "operator history remain with the underwriter."),
            "coverage": ("Trackable objects (>~10 cm). Lethal non-trackable "
                         "1-10 cm debris is NOT included — see debris_flux."),
        },
    }


def methodology() -> Dict[str, Any]:
    """Transparent description of what is computed and from what."""
    return {
        "components": {
            "catalogue_state": "Live Space-Track catalogue, split debris/rocket-body/payload",
            "kinetic_burden": "rho_threat * v_rel * A_ref (10 m^2), threat = non-manoeuvrable only",
            "environment_trend": f"Threat-population change {TREND_FIRST_YEAR}-{TREND_LAST_YEAR} from CAS archive",
            "cascade_exposure": "NASA Standard Breakup Model + drag-limited cloud clearing",
            "debris_flux": "RESERVED for ESA MASTER-8 (lethal non-trackable) — not yet integrated",
        },
        "trend_modes": {
            "inclination-gated": "Counts only objects whose plane crosses yours within tolerance",
            "band-based": "Counts all objects in the altitude shell regardless of inclination",
        },
        "limits": [
            "No covariance in public catalogues -> no satellite-specific Pc",
            "Trackable objects only (>~10 cm)",
            "Archive is weekly-sampled; trend is a relative measure",
            "CAS is not an actuary and does not set premiums",
        ],
    }
