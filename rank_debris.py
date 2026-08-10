#!/usr/bin/env python3
"""
CAS — Top LEO Debris Threats ranker.

Reads distinct CDMs from conjunction_events, classifies each counterparty
as debris via name pattern, computes per-debris ranking metrics, bands by
altitude (from Space-Track catalog cache), writes to leo_debris_ranking
for the current ISO week. Pure-function core is testable.
"""
import json, os, sys, re, datetime
from collections import defaultdict

def _dsn():
    import os as _os
    v = _os.environ.get("DB_URL")
    if v:
        return v
    e = {}
    with open("/opt/cas/.env") as f:
        for ln in f:
            if "=" in ln and not ln.startswith("#"):
                k, val = ln.strip().split("=", 1)
                e[k] = val.strip().strip('"').strip("'")
    return e["DB_URL"]

DB_URL = _dsn()
ST_CACHE = "/opt/cas/.spacetrack_catalog_cache.json"

DEBRIS_PATTERN = re.compile(r" DEB\b|\bR/B\b| DEBRIS\b", re.IGNORECASE)

# Altitude bands (km, average altitude)
BAND_LOW    = (500, 600)
BAND_MID    = (1000, 1200)


def is_debris(name: str) -> bool:
    """Name pattern heuristic for debris / rocket body detection."""
    if not name:
        return False
    return bool(DEBRIS_PATTERN.search(name))


def classify_band(altitude_km):
    """Return 'low', 'mid', or None for altitude band membership."""
    if altitude_km is None:
        return None
    if BAND_LOW[0] <= altitude_km <= BAND_LOW[1]:
        return "low"
    if BAND_MID[0] <= altitude_km <= BAND_MID[1]:
        return "mid"
    return None


def load_st_altitudes():
    """Load Space-Track catalog and compute avg altitude per NORAD ID.

    Uses TLE mean motion to derive semi-major axis, then perigee/apogee,
    then average altitude above Earth's surface.
    """
    try:
        with open(ST_CACHE, "r") as f:
            cache = json.load(f)
    except Exception as e:
        print(f"[WARN] could not read ST cache: {e}")
        return {}

    altitudes = {}
    MU = 398600.4418  # km^3/s^2
    R_EARTH = 6378.137  # km

    import math
    for kind in ("debris", "rocket_body"):
        for obj in cache.get(kind, []):
            norad = str(obj.get("norad", ""))
            l2 = obj.get("l2", "")
            if not norad or not l2 or len(l2) < 63:
                continue
            try:
                # TLE line 2, cols 53-63: mean motion (revs/day)
                mm = float(l2[52:63])
                ecc = float("0." + l2[26:33].strip())
                n_rad_per_s = mm * 2 * math.pi / 86400.0
                a = (MU / (n_rad_per_s ** 2)) ** (1/3)  # km
                perigee = a * (1 - ecc) - R_EARTH
                apogee = a * (1 + ecc) - R_EARTH
                avg_alt = (perigee + apogee) / 2.0
                altitudes[norad] = avg_alt
            except Exception:
                continue
    print(f"[rank] Loaded altitudes for {len(altitudes)} objects")
    return altitudes



def compute_adr_priority(cdm_count, unique_counterparties, max_pc, cumulative_pc, altitude_km, object_name):
    """
    ADR (Active Debris Removal) Priority Score — multi-factor.

    Answers: "With limited resources, which debris should we remove FIRST?"
    Higher score = higher removal priority. Aligned with ESA Zero Debris logic.

    Factors (each normalized, then weighted):
      - threat_frequency: how often this object generates conjunctions (persistent menace)
      - mission_spread:   how many DISTINCT satellites it threatens (systemic risk)
      - severity:         peak + cumulative collision probability
      - persistence:      altitude-driven orbital lifetime (high alt = stays for centuries
                          => higher removal priority; low alt = natural decay soon)
      - mass_proxy:       rocket bodies / large debris => many fragments if hit
                          (catastrophic potential — Kessler contribution)
    """
    import math

    # --- threat frequency (log-scaled, saturates) ---
    freq = math.log1p(cdm_count) / math.log1p(750.0)  # ~0..1 over observed range
    freq = min(1.0, freq)

    # --- mission spread (distinct counterparties) ---
    spread = math.log1p(unique_counterparties) / math.log1p(50.0)
    spread = min(1.0, spread)

    # --- severity (peak Pc dominant, cumulative as booster) ---
    # Pc near 1e-1 is extreme; 1e-4 is routine. Log-scale.
    if max_pc and max_pc > 0:
        sev_peak = (math.log10(max_pc) + 6) / 6.0   # 1e-6..1e0 -> 0..1
        sev_peak = max(0.0, min(1.0, sev_peak))
    else:
        sev_peak = 0.0
    sev_cum = min(1.0, (cumulative_pc or 0) / 0.5)
    severity = 0.7 * sev_peak + 0.3 * sev_cum

    # --- persistence (altitude-driven orbital lifetime) ---
    # Below ~500km: years (natural decay) -> low removal priority
    # 500-800km: decades -> medium
    # 800-1200km+: centuries -> high removal priority
    if altitude_km is None:
        persistence = 0.5  # unknown -> neutral
    elif altitude_km < 500:
        persistence = 0.2
    elif altitude_km < 700:
        persistence = 0.45
    elif altitude_km < 900:
        persistence = 0.75
    elif altitude_km < 1200:
        persistence = 1.0
    else:
        persistence = 0.9  # very high but less congested

    # --- mass / fragmentation proxy (rocket bodies are large) ---
    name_u = (object_name or "").upper()
    if "R/B" in name_u:
        mass_proxy = 1.0       # rocket body — large, many fragments
    elif "DEB" in name_u:
        mass_proxy = 0.4       # fragment — smaller
    else:
        mass_proxy = 0.6       # unknown intact object

    # --- weighted ADR priority (0..100) ---
    score = (
        0.28 * freq +
        0.24 * spread +
        0.22 * severity +
        0.16 * persistence +
        0.10 * mass_proxy
    ) * 100.0

    return round(score, 2), {
        "freq": round(freq, 3),
        "spread": round(spread, 3),
        "severity": round(severity, 3),
        "persistence": round(persistence, 3),
        "mass_proxy": round(mass_proxy, 3),
    }


def compute_rankings(cdms, altitudes):
    """Pure function: CDM list + altitude map -> ranking structure.

    Args:
        cdms: list of dicts with keys: cdm_id, sat1, sat2, norad1, norad2,
              pc, fetched_at (datetime or str)
        altitudes: dict norad_id (str) -> avg altitude km

    Returns: dict with keys 'all', 'low', 'mid', each a list of ranked
             debris dicts sorted by threat metric.
    """
    # Deduplicate by cdm_id — keep latest fetched_at
    by_id = {}
    for c in cdms:
        cid = c.get("cdm_id")
        if not cid:
            continue
        if cid not in by_id or (c.get("fetched_at") and str(c["fetched_at"]) > str(by_id[cid].get("fetched_at", ""))):
            by_id[cid] = c
    uniq = list(by_id.values())

    # Accumulate per-debris metrics
    # debris_key = norad_id (string)
    metrics = defaultdict(lambda: {
        "name": None, "norad": None,
        "cdm_count": 0,
        "counterparties": set(),
        "max_pc": 0.0,
        "cumulative_pc": 0.0,
        "first_seen": None, "last_seen": None,
    })

    for c in uniq:
        pc = float(c.get("pc") or 0)
        fa = c.get("fetched_at")
        pairs = [
            (c.get("sat1"), c.get("norad1"), c.get("sat2"), c.get("norad2")),
            (c.get("sat2"), c.get("norad2"), c.get("sat1"), c.get("norad1")),
        ]
        for self_name, self_norad, other_name, other_norad in pairs:
            if not is_debris(self_name):
                continue
            if not self_norad or self_norad == "?":
                continue
            key = str(self_norad)
            m = metrics[key]
            m["name"] = (self_name or "").strip()
            m["norad"] = key
            m["cdm_count"] += 1
            if other_norad and other_norad != "?":
                m["counterparties"].add(str(other_norad))
            if pc > m["max_pc"]:
                m["max_pc"] = pc
            m["cumulative_pc"] += pc
            if fa:
                if m["first_seen"] is None or str(fa) < str(m["first_seen"]):
                    m["first_seen"] = fa
                if m["last_seen"] is None or str(fa) > str(m["last_seen"]):
                    m["last_seen"] = fa

    # Convert to list + compute threat score
    # threat_score = unique_counterparties * 1000 + cumulative_pc * 1e6
    # (primary: counterparty count, tiebreaker: cumulative Pc)
    out = []
    for key, m in metrics.items():
        alt = altitudes.get(key)
        adr_score, adr_factors = compute_adr_priority(
            m["cdm_count"], len(m["counterparties"]),
            m["max_pc"], m["cumulative_pc"], alt, m["name"]
        )
        entry = {
            "norad_id": key,
            "object_name": m["name"] or "UNKNOWN",
            "cdm_count": m["cdm_count"],
            "unique_counterparties": len(m["counterparties"]),
            "max_pc": m["max_pc"],
            "cumulative_pc": m["cumulative_pc"],
            # threat_score column now carries the ADR Priority Score (0..100)
            "threat_score": adr_score,
            "adr_factors": adr_factors,
            "avg_altitude_km": alt,
            "first_seen": m["first_seen"],
            "last_seen": m["last_seen"],
            "band": classify_band(alt),
        }
        out.append(entry)

    # Sort by ADR Priority Score desc (removal priority)
    out.sort(key=lambda x: x["threat_score"], reverse=True)

    # Partition by band
    rankings = {
        "all": out,
        "low": [e for e in out if e["band"] == "low"],
        "mid": [e for e in out if e["band"] == "mid"],
    }
    return rankings


def write_rankings_to_db(rankings, limit=25):
    """Write top-N rankings per band to leo_debris_ranking for current ISO week."""
    import psycopg2
    # ISO week Monday
    today = datetime.date.today()
    monday = today - datetime.timedelta(days=today.weekday())

    conn = psycopg2.connect(DB_URL)
    cur = conn.cursor()

    # Clear current week for idempotency
    cur.execute("DELETE FROM leo_debris_ranking WHERE snapshot_week = %s", (monday,))

    total = 0
    for band, entries in rankings.items():
        for rank, e in enumerate(entries[:limit], start=1):
            cur.execute("""
                INSERT INTO leo_debris_ranking
                  (snapshot_week, band, rank, norad_id, object_name,
                   cdm_count, unique_counterparties, max_pc, cumulative_pc,
                   threat_score, avg_altitude_km, first_seen, last_seen)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """, (
                monday, band, rank, e["norad_id"], e["object_name"],
                e["cdm_count"], e["unique_counterparties"],
                e["max_pc"] or None, e["cumulative_pc"] or None,
                e["threat_score"], e["avg_altitude_km"],
                e["first_seen"], e["last_seen"],
            ))
            total += 1

    conn.commit()
    cur.close()
    conn.close()
    return total, monday


def load_cdms_from_db():
    """Read distinct CDMs from conjunction_events."""
    import psycopg2
    conn = psycopg2.connect(DB_URL)
    cur = conn.cursor()
    cur.execute("""
        SELECT DISTINCT ON (cdm_id)
            cdm_id, sat1, sat2, norad1, norad2, pc, fetched_at
        FROM conjunction_events
        ORDER BY cdm_id, fetched_at DESC
    """)
    rows = cur.fetchall()
    cur.close(); conn.close()
    return [
        {"cdm_id": r[0], "sat1": r[1], "sat2": r[2],
         "norad1": r[3], "norad2": r[4], "pc": r[5], "fetched_at": r[6]}
        for r in rows
    ]


def main():
    print("[rank] Loading ST altitudes...")
    altitudes = load_st_altitudes()
    print("[rank] Loading CDMs from DB...")
    cdms = load_cdms_from_db()
    print(f"[rank] {len(cdms)} distinct CDMs loaded")
    print("[rank] Computing rankings...")
    rankings = compute_rankings(cdms, altitudes)
    for band in ("all", "low", "mid"):
        print(f"[rank] band={band}: {len(rankings[band])} debris entries")
    print("[rank] Writing to DB...")
    total, week = write_rankings_to_db(rankings)
    print(f"[rank] Wrote {total} ranking rows for week of {week}")
    print("[rank] DONE")


if __name__ == "__main__":
    main()
