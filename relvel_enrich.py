#!/usr/bin/env python3
"""
CAS — Relative Velocity Enrichment
Public CDM'lerde RELATIVE_SPEED yok -> SGP4 ile iki nesneyi TCA'ya propagate edip
|v1-v2| (m/s) hesaplar, raw_json.relative_velocity_ms + _relvel_src yazar. Idempotent.
TLE kaynagi: yalnizca yerel catalog cache (.spacetrack_catalog_cache.json;
     debris + rocket body + payload + unknown -- refresh_catalog_cache.py dordunu de
     yaziyor, okuyan taraf 2026-08-26'ya kadar "unknown"i dusuruyordu) ve watchlist.
     CelesTrak CANLI KAYNAK DEGIL:
     per-NORAD fallback RELVEL_CELESTRAK ile kapali (varsayilan "0") ve oyle
     kalmali -- bu sunucu 24 Mayis 2026'dan beri CelesTrak firewall'unda.
     Operator-tier (RELATIVE_SPEED dolu) kayitlar atlanir.

Gercek cron (verified 2026-08-19, /etc/cron.d/cas-relvel-enrich):
    25 0,8,16 * * * root /usr/bin/python3 /opt/cas/relvel_enrich.py >> /var/log/cas/relvel_enrich.log 2>&1

Saatlik DEGIL: fetch_cdm.py'nin 0,8,16 slotlarindan 25 dakika sonra kosar,
yani yeni CDM'ler yazildiktan sonra. Docstring eskiden "25 * * * *" diyordu.
"""
import os, sys, json, math, datetime, ssl, urllib.request
import psycopg2
from psycopg2.extras import Json
from sgp4.api import Satrec, jday

_CAS_HOME = os.environ.get("CAS_HOME", "/opt/cas").rstrip("/") or "/opt/cas"

def _dsn():
    import os as _o
    v = _o.environ.get("DB_URL")
    if v: return v
    e = {}
    with open(_o.path.join(_CAS_HOME, ".env")) as f:
        for ln in f:
            if "=" in ln and not ln.startswith("#"):
                k, val = ln.strip().split("=", 1)
                e[k] = val.strip().strip('"').strip("'")
    return e["DB_URL"]


DB_URL     = _dsn()
CACHE      = os.path.join(_CAS_HOME, ".spacetrack_catalog_cache.json")
LOOKBACK_H = int(os.environ.get("RELVEL_LOOKBACK_H") or "72")
BATCH      = int(os.environ.get("RELVEL_BATCH") or "300")
GROUPS     = [g for g in os.environ.get("RELVEL_GROUPS", "").split(",") if g.strip()]
USE_CELES  = os.environ.get("RELVEL_CELESTRAK", "0") == "1"  # per-NORAD son çare, varsayılan kapalı

# TCA geometry gate. A CDM describes an encounter; if the two TLEs we resolved put
# the objects this far apart at TCA, they are not describing that encounter and the
# |v1-v2| computed from them is silently wrong. Measured 2026-08-26 over 30 days of
# production events (2026-07-27..08-26): 2,146 pairs resolvable from the cache.
#   - CDM MISS_DISTANCE is <= 0.95 km at p99, so every km of TCA separation we see
#     is TLE/propagation error, not the encounter.
#   - Operationally representative subset = TLE-epoch -> TCA span <= 10 d (what the
#     72 h lookback plus a daily cache refresh actually produces), n=1,018:
#     p50 5.8, p90 12.6, p95 15.3, p97 18.2 km. The sorted tail then reads
#     ... 35.6, 58.5, 70.5 | 174.6, 640, 2,218, 4,827, 7,219, 12,096 km --
#     an empirical gap between propagation blur and broken geometry.
#   - Any cut inside (70.5, 174.6] rejects the identical 11 pairs (1.08%); 150 takes
#     the top of the gap, the widest margin over the good population (max 84.8 km in
#     the 7-day set the pre-fix code fills) that still catches every gross failure --
#     including 42153 x 81065 at 4,481 km, whose CDM miss distance is 0.10 km and
#     whose TLE epoch is 1,096 days old (Space-Track has nothing newer; refreshing
#     does not fix it).
# Rejected pairs are left untouched rather than marked, so a later element set can
# still rescue them while they remain inside the lookback window.
MAX_SEP_KM = float(os.environ.get("RELVEL_MAX_SEP_KM") or "150")
MISS_LOG_N = int(os.environ.get("RELVEL_MISS_LOG_N") or "20")  # unresolved NORADs to name in the log
DRYRUN     = "--dryrun" in sys.argv
_ctx = ssl.create_default_context()  # TLS verification enabled (default context)

def log(m):
    ts = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    print(f"[{ts}] {m}", flush=True)

# refresh_catalog_cache.py fetches and writes all four OBJECT_TYPE buckets; read all
# four. Dropping "unknown" cost 18.8% of a 21-day window (301 of 1,605 events) to
# miss_tle -- the counterpart of every unmatched event sat in exactly that bucket.
CACHE_BUCKETS = ("debris", "rocket_body", "payload", "unknown")

def load_cache():
    idx = {}
    try:
        c = json.load(open(CACHE))
        per = []
        for k in CACHE_BUCKETS:
            n = 0
            for o in c.get(k, []):
                nid = str(o.get("norad") or "").strip()
                if nid and o.get("l1") and o.get("l2"): idx[nid] = (o["l1"], o["l2"]); n += 1
            per.append(f"{k}={n}")
        log("cache buckets: " + " ".join(per))
    except Exception as e: log(f"cache error: {e}")
    return idx

def load_group(group):
    idx = {}
    try:
        req = urllib.request.Request(f"https://celestrak.org/NORAD/elements/gp.php?GROUP={group}&FORMAT=TLE",
                                     headers={"User-Agent": "CAS/1.0"})
        txt = urllib.request.urlopen(req, timeout=30, context=_ctx).read().decode("utf-8", "replace")
        lines = [l.rstrip() for l in txt.splitlines() if l.strip()]
        for i in range(len(lines)-1):
            if lines[i].startswith("1 ") and lines[i+1].startswith("2 "):
                nid = lines[i][2:7].strip()
                if nid: idx[nid] = (lines[i], lines[i+1])
    except Exception as e: log(f"group {group} error: {e}")
    return idx

def load_watchlist(conn):
    idx = {}
    try:
        cur = conn.cursor()
        cur.execute("SELECT norad_id, tle_line1, tle_line2 FROM watchlist WHERE tle_line1 IS NOT NULL AND tle_line2 IS NOT NULL")
        for nid, l1, l2 in cur.fetchall(): idx[str(nid).strip()] = (l1, l2)
        cur.close()
    except Exception as e: log(f"watchlist error: {e}")
    return idx

_cc = {}
def celestrak_one(norad):
    if not USE_CELES: return None
    if norad in _cc: return _cc[norad]
    res = None
    try:
        req = urllib.request.Request(f"https://celestrak.org/NORAD/elements/gp.php?CATNR={norad}&FORMAT=JSON",
                                     headers={"User-Agent": "CAS/1.0"})
        gp = json.loads(urllib.request.urlopen(req, timeout=10, context=_ctx).read().decode())
        if gp and gp[0].get("TLE_LINE1") and gp[0].get("TLE_LINE2"):
            res = (gp[0]["TLE_LINE1"], gp[0]["TLE_LINE2"])
    except Exception: res = None
    _cc[norad] = res; return res

def parse_tca(s):
    if not s: return None
    s = s.replace("T", " ").split(".")[0].strip()
    try: return datetime.datetime.strptime(s, "%Y-%m-%d %H:%M:%S")
    except Exception: return None

def state(tle, dt):
    """(r_km, v_km_s) at dt, or None when SGP4 refuses the element set."""
    try:
        sat = Satrec.twoline2rv(tle[0], tle[1])
        jd, fr = jday(dt.year, dt.month, dt.day, dt.hour, dt.minute, dt.second)
        e, r, v = sat.sgp4(jd, fr)
        return (r, v) if e == 0 else None
    except Exception: return None

def rel_ms(t1, t2, dt):
    """(relative_speed_ms, tca_separation_km); (None, None) when either SGP4 fails."""
    s1 = state(t1, dt); s2 = state(t2, dt)
    if s1 is None or s2 is None: return None, None
    (r1, v1), (r2, v2) = s1, s2
    dv = (v1[0]-v2[0], v1[1]-v2[1], v1[2]-v2[2])
    dr = (r1[0]-r2[0], r1[1]-r2[1], r1[2]-r2[2])
    return (round(math.sqrt(dv[0]**2+dv[1]**2+dv[2]**2) * 1000.0, 1),
            round(math.sqrt(dr[0]**2+dr[1]**2+dr[2]**2), 1))

def main():
    conn = psycopg2.connect(DB_URL); cur = conn.cursor()
    cache = load_cache(); wl = load_watchlist(conn)
    bulk = {}
    for g in GROUPS:
        gi = load_group(g); bulk.update(gi); log(f"group {g}: {len(gi)}")
    log(f"TLE: cache={len(cache)} bulk={len(bulk)} watchlist={len(wl)} per-norad={'on' if USE_CELES else 'off'} dryrun={DRYRUN}")
    def resolve(n):
        n = str(n).strip()
        return cache.get(n) or bulk.get(n) or wl.get(n) or celestrak_one(n)
    cur.execute("""
        SELECT id, raw_json FROM conjunction_events
        WHERE fetched_at > NOW() - (%s * INTERVAL '1 hour')
          AND COALESCE((raw_json->>'relative_velocity_ms')::float, 0) = 0
          AND NOT (raw_json::jsonb ? '_relvel_src')
        ORDER BY fetched_at DESC LIMIT %s
    """, (LOOKBACK_H, BATCH))
    rows = cur.fetchall()
    log(f"candidates={len(rows)}")
    filled = miss_tle = no_tca = sgp4_fail = geom_fail = 0
    # miss_tle was a bare counter for three months and never recorded which object
    # failed to resolve, so every diagnosis started from zero. Name them.
    miss_norads = {}   # unresolved norad -> events it blocked
    geom_seen = []     # first few gate rejections, for the same reason
    for rid, rj in rows:
        if isinstance(rj, str):
            try: rj = json.loads(rj)
            except Exception: continue
        n1, n2 = rj.get("norad1"), rj.get("norad2")
        tca = parse_tca(rj.get("tca_str") or (rj.get("_raw_st_cdm") or {}).get("TCA"))
        if not tca: no_tca += 1; continue
        t1 = resolve(n1); t2 = resolve(n2)
        if not t1 or not t2:
            miss_tle += 1
            for n, t in ((n1, t1), (n2, t2)):
                if not t: miss_norads[str(n).strip()] = miss_norads.get(str(n).strip(), 0) + 1
            continue
        rv, sep = rel_ms(t1, t2, tca)
        if rv is None: sgp4_fail += 1; continue
        if sep > MAX_SEP_KM:
            # Ephemerides disagree about where the encounter is -> |v1-v2| is meaningless.
            geom_fail += 1
            if len(geom_seen) < 8: geom_seen.append(f"{n1}x{n2} sep={sep}km")
            continue
        filled += 1
        if DRYRUN:
            if filled <= 8: log(f"id={rid} {n1}x{n2} rel_v={rv} m/s ({rv/1000:.2f} km/s) sep={sep} km")
        else:
            rj["relative_velocity_ms"] = rv; rj["_relvel_src"] = "sgp4"
            cur.execute("UPDATE conjunction_events SET raw_json=%s WHERE id=%s", (Json(rj), rid)); conn.commit()
    cur.close(); conn.close()
    if miss_norads:
        top = sorted(miss_norads.items(), key=lambda kv: -kv[1])[:MISS_LOG_N]
        log(f"miss_tle NORADs ({len(miss_norads)} unique, top {len(top)}): "
            + " ".join(f"{n}x{c}" for n, c in top))
    if geom_seen:
        log(f"geom_fail>{MAX_SEP_KM}km sample: " + " ".join(geom_seen))
    log(f"DONE filled={filled} miss_tle={miss_tle} no_tca={no_tca} sgp4_fail={sgp4_fail} geom_fail={geom_fail}")
    return 0

if __name__ == "__main__":
    try: sys.exit(main())
    except Exception as e:
        log(f"FATAL: {type(e).__name__}: {e}"); sys.exit(1)
