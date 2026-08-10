"""OCBI — Orbital Collision-Burden Index.

Composite orbital burden index built as a layer ON TOP OF the production
kinetic burden (orbital_risk.kinetic_burden). Lambda is NEVER recomputed here:
it is imported, so the insurance report and OCBI can never silently diverge.

    OCBI = Lambda_phys * kappa * (1 + ALPHA*S) * (1 + BETA*C) * (1 + GAMMA*T)

  Lambda_phys : rho_threat * v_rel * A_ref          [orbital_risk.kinetic_burden]
  kappa       : pay-normalised observation calibration
                (observed conjunction share / Lambda share) - NOT an absolute ratio
  S           : severity, strict miss-distance thresholds {200/100/50/25 m}
  C           : cascade exposure = log-normalised(pool x 90%-cloud-clearing days)
  T           : environment trend  - UNAVAILABLE until backfill (see IS-NOT)

IS      : a relative, catalogue-derived measure of collective orbital burden
          for an altitude/inclination band.
IS NOT  : an actuarial rate, a premium, a per-satellite collision probability,
          or a substitute for an underwriter's judgement. CAS produces the
          collective orbital burden; the underwriter adds insured value,
          launcher record and operator record.
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Sequence, Tuple

from services import mission_design as md
from services.orbital_risk import kinetic_burden

import vleo

# --- tunables ---------------------------------------------------------------
BAND_HALF_KM = 25.0          # Lambda band half-width (matches production assess_orbit)
CASCADE_POOL_HALF_KM = 50.0  # fragmentation clouds spread into neighbouring bands
INC_TOL_DEG = 5.0
ALPHA = 1.0                  # severity weight
BETA = 0.5                   # cascade weight
GAMMA = 0.5                  # trend weight

SEVERITY_THRESHOLDS_M: Dict[int, int] = {200: 1, 100: 2, 50: 4, 25: 8}

# kappa is derived from a ~4-month observation window and is therefore a LOW
# confidence term. It is clipped so that observation calibration stays a
# CORRECTION to the physics rather than overwhelming it. Clipping is always
# reported (kappa_clipped / kappa_raw) - it is never applied silently.
KAPPA_MIN = 0.5
KAPPA_MAX = 2.0


# --- data access ------------------------------------------------------------
def load_conjunction_altitudes(
        catalog: Optional[List[Dict[str, Any]]] = None
) -> List[Tuple[float, Optional[float]]]:
    """Load observed conjunctions as (mean_altitude_km, miss_distance_m).

    Altitude is the mean of the two objects' catalogue altitudes; events where
    neither object resolves against the catalogue are dropped. Rows are
    de-duplicated by CDM id.

    NOTE ON THE OBSERVATION BASIS: stored conjunctions were retrieved with a
    PC > 1e-4 filter, so this is a high-risk subset, not a representative
    sample of all close approaches. kappa and severity therefore describe the
    distribution *within that subset*. The filter is applied uniformly to every
    band, so comparisons between bands remain valid; absolute values do not
    describe the full conjunction population.
    """
    from core.database import get_dict_cursor

    cat = catalog if catalog is not None else md._load_catalog()
    alt_by_norad = {c["norad"]: c["alt"] for c in cat}

    with get_dict_cursor() as cur:
        # One row per CDM. The table stores a row per fetch, so a message that
        # stayed inside the query window for days appears many times. Counting
        # rows would measure fetch history, not observation density.
        cur.execute("SELECT DISTINCT ON (cdm_id) norad1, norad2, miss_dist_m "
                    "FROM conjunction_events ORDER BY cdm_id, fetched_at")
        rows = cur.fetchall()

    out: List[Tuple[float, Optional[float]]] = []
    for r in rows:
        alts = [alt_by_norad.get(str(r[c]).strip()) for c in ("norad1", "norad2")]
        alts = [a for a in alts if a is not None]
        if not alts:
            continue
        m = r["miss_dist_m"]
        out.append((sum(alts) / len(alts), float(m) if m is not None else None))
    return out


# --- components -------------------------------------------------------------
def severity_strict(miss_distances_m: Sequence[Optional[float]]) -> Dict[str, Any]:
    """Strict threshold severity in [0,1].

    Each threshold contributes weight * (fraction of events below it); the sum
    is normalised by total weight. Nested thresholds are intentional: a 25 m
    event counts in all four buckets.
    """
    ms = [float(m) for m in miss_distances_m if m is not None]
    if not ms:
        return {"severity": None, "n_events": 0, "median_miss_m": None,
                "buckets": {}, "available": False}

    n = len(ms)
    buckets = {d: sum(1 for m in ms if m < d) for d in SEVERITY_THRESHOLDS_M}
    total_w = sum(SEVERITY_THRESHOLDS_M.values())
    sev = sum(w * (buckets[d] / n) for d, w in SEVERITY_THRESHOLDS_M.items()) / total_w

    return {
        "severity": sev,
        "n_events": n,
        "median_miss_m": sorted(ms)[n // 2],
        "buckets": {f"under_{d}m": buckets[d] for d in sorted(buckets, reverse=True)},
        "available": True,
    }


def cascade_raw(catalog: List[Dict[str, Any]], altitude_km: float) -> Dict[str, Any]:
    """Raw cascade exposure components: exposed pool x cloud persistence.

    pool         : catalogue objects within +/-CASCADE_POOL_HALF_KM
    clearing_days: 90% cloud-clearing time (NASA SBM + drag, via vleo)

    Returns raw components only; normalisation to [0,1] needs the peer
    population and therefore happens in ocbi_batch().
    """
    pool = md._catalog_band_stats(
        catalog, altitude_km, CASCADE_POOL_HALF_KM, None, INC_TOL_DEG
    )["total"]

    cz = vleo.assess_vleo_cascade(altitude_km)
    clearing = cz.get("cloud_clearing") or {}
    days = clearing.get("ninety_percent_days")
    source = "vleo.cloud_clearing.ninety_percent_days"

    if days is None:
        days = vleo.estimate_orbital_lifetime(
            altitude_km, ballistic_coef=50.0, f107_flux=150.0
        )
        source = "fallback:estimate_orbital_lifetime(bc=50)"

    return {
        "pool": pool,
        "clearing_days_90pct": float(days),
        "raw_product": pool * float(days),
        "source": source,
        "catastrophic": cz.get("catastrophic"),
        "self_cleaning": cz.get("self_cleaning"),
    }


def kappa_pay_normalised(observed_share: float, lambda_share: float) -> Optional[float]:
    """Observation calibration: how over/under-represented a band is in the
    observed conjunction record relative to its physical burden share.

    kappa = 1.0 means the band is observed exactly in proportion to its
    physical burden. Deliberately a SHARE ratio, not an absolute count ratio.

    Returns both the clipped value used in the index and the raw value, so a
    clipped band (e.g. a heavily-tracked constellation shell) stays visible.
    """
    if lambda_share <= 0:
        return {"kappa": None, "kappa_raw": None, "clipped": False}
    raw = observed_share / lambda_share
    clipped = min(max(raw, KAPPA_MIN), KAPPA_MAX)
    return {"kappa": clipped, "kappa_raw": raw,
            "clipped": clipped != raw,
            "bounds": [KAPPA_MIN, KAPPA_MAX]}


# --- batch driver -----------------------------------------------------------
def ocbi_batch(orbits: Sequence[Tuple[str, float, Optional[float]]],
               conjunctions: Sequence[Tuple[float, Optional[float]]],
               trend_by_orbit: Optional[Dict[str, float]] = None,
               catalog: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """Compute OCBI for a peer set of orbits.

    OCBI is RELATIVE: kappa and C are normalised across the supplied peer set,
    so a single orbit cannot be scored in isolation. Pass at least 3 orbits.

    orbits        : [(name, altitude_km, inclination_deg or None), ...]
    conjunctions  : [(mean_altitude_km, miss_distance_m or None), ...]
    trend_by_orbit: optional {name: trend_fraction}; missing -> T UNAVAILABLE
    """
    if len(orbits) < 3:
        raise ValueError("OCBI is a relative index; supply at least 3 peer orbits.")

    cat = catalog if catalog is not None else md._load_catalog()
    trend_by_orbit = trend_by_orbit or {}

    stage: List[Dict[str, Any]] = []
    for name, alt, inc in orbits:
        st = md._catalog_band_stats(cat, alt, BAND_HALF_KM, inc, INC_TOL_DEG)
        burden = kinetic_burden(alt, st["non_maneuverable"], BAND_HALF_KM)

        lo, hi = alt - BAND_HALF_KM, alt + BAND_HALF_KM
        in_band = [m for (a, m) in conjunctions if lo <= a <= hi]

        stage.append({
            "orbit": name,
            "altitude_km": alt,
            "inclination_deg": inc,
            "catalogue": st,
            "burden": burden,
            "observed_events": len(in_band),
            "severity": severity_strict(in_band),
            "cascade_raw": cascade_raw(cat, alt),
        })

    sum_lambda = sum(s["burden"]["lambda_per_year"] for s in stage) or 1.0
    sum_observed = sum(s["observed_events"] for s in stage) or 1.0

    logs = [math.log10(max(s["cascade_raw"]["raw_product"], 1.0)) for s in stage]
    c_lo, c_hi = min(logs), max(logs)
    c_span = (c_hi - c_lo) if c_hi > c_lo else 0.0

    results: List[Dict[str, Any]] = []
    for s, log_c in zip(stage, logs):
        lam = s["burden"]["lambda_per_year"]
        k = kappa_pay_normalised(
            s["observed_events"] / sum_observed, lam / sum_lambda
        )
        kappa = k["kappa"]
        sev = s["severity"]["severity"]
        c_hat = ((log_c - c_lo) / c_span) if c_span > 0 else 0.0
        trend = trend_by_orbit.get(s["orbit"])

        ocbi = lam
        ocbi *= kappa if kappa is not None else 1.0
        ocbi *= (1.0 + ALPHA * sev) if sev is not None else 1.0
        ocbi *= (1.0 + BETA * c_hat)
        ocbi *= (1.0 + GAMMA * trend) if trend is not None else 1.0

        results.append({
            **s,
            "kappa": kappa,
            "kappa_raw": k["kappa_raw"],
            "kappa_clipped": k["clipped"],
            "c_hat": c_hat,
            "trend": trend,
            "ocbi": ocbi,
            "unavailable": [k for k, v in (
                ("kappa", kappa), ("severity", sev), ("trend", trend)
            ) if v is None],
        })

    scores = sorted(r["ocbi"] for r in results)
    for r in results:
        r["percentile_in_peer_set"] = round(
            100.0 * sum(1 for x in scores if x <= r["ocbi"]) / len(scores)
        )

    results.sort(key=lambda r: -r["ocbi"])
    return {
        "results": results,
        "peer_set_size": len(results),
        "catalogue_objects": len(cat),
        "catalogue_source": md.catalog_source(),
        "conjunction_events_used": len(conjunctions),
        "weights": {"alpha": ALPHA, "beta": BETA, "gamma": GAMMA},
        "confidence": {
            "lambda": "HIGH - physics, production kinetic_burden",
            "kappa": (f"LOW - short observation window; clipped to "
                      f"[{KAPPA_MIN}, {KAPPA_MAX}]"),
            "severity": "MEDIUM - public CDM miss distances",
            "cascade": "MEDIUM - NASA SBM + drag physics",
            "trend": ("MEDIUM - supplied" if trend_by_orbit
                      else "UNAVAILABLE - awaiting GP_History backfill"),
        },
        "observation_basis": ("stored conjunctions are filtered at PC > 1e-4; "
                              "kappa and severity describe that high-risk "
                              "subset, applied uniformly across all bands"),
        "is": "relative collective orbital burden across the supplied peer set",
        "is_not": ("an actuarial rate, a premium, a per-satellite collision "
                   "probability, or a substitute for underwriter judgement"),
    }
