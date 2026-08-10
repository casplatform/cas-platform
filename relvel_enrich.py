#!/usr/bin/env python3
"""
CAS — Relative Velocity Enrichment
Public CDM'lerde RELATIVE_SPEED yok -> SGP4 ile iki nesneyi TCA'ya propagate edip
|v1-v2| (m/s) hesaplar, raw_json.relative_velocity_ms + _relvel_src yazar. Idempotent.
TLE: catalog cache (debris+RB) + Celestrak GROUP=active (payloads) + watchlist
     + (ops.) per-NORAD Celestrak fallback. Operator-tier (RELATIVE_SPEED dolu) atlanır.
Cron: 25 * * * * root /usr/bin/python3 /opt/cas/relvel_enrich.py >> /var/log/cas/relvel_enrich.log 2>&1
"""
import os, sys, json, math, datetime, ssl, urllib.request
import psycopg2
from psycopg2.extras import Json
from sgp4.api import Satrec, jday

def _dsn():
    import os as _o
    v = _o.environ.get("DB_URL")
    if v: return v
    e = {}
    with open("/opt/cas/.env") as f:
        for ln in f:
            if "=" in ln and not ln.startswith("#"):
                k, val = ln.strip().split("=", 1)
                e[k] = val.strip().strip('"').strip("'")
    return e["DB_URL"]


DB_URL     = _dsn()
CACHE      = "/opt/cas/.spacetrack_catalog_cache.json"
LOOKBACK_H = int(os.environ.get("RELVEL_LOOKBACK_H", "72"))
BATCH      = int(os.environ.get("RELVEL_BATCH", "300"))
GROUPS     = [g for g in os.environ.get("RELVEL_GROUPS", "").split(",") if g.strip()]
USE_CELES  = os.environ.get("RELVEL_CELESTRAK", "0") == "1"  # per-NORAD son çare, varsayılan kapalı
DRYRUN     = "--dryrun" in sys.argv
_ctx = ssl.create_default_context(); _ctx.check_hostname = False; _ctx.verify_mode = ssl.CERT_NONE

def log(m):
    ts = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    print(f"[{ts}] {m}", flush=True)

def load_cache():
    idx = {}
    try:
        c = json.load(open(CACHE))
        for k in ("debris", "rocket_body", "payload"):
            for o in c.get(k, []):
                nid = str(o.get("norad") or "").strip()
                if nid and o.get("l1") and o.get("l2"): idx[nid] = (o["l1"], o["l2"])
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

def vel(tle, dt):
    try:
        sat = Satrec.twoline2rv(tle[0], tle[1])
        jd, fr = jday(dt.year, dt.month, dt.day, dt.hour, dt.minute, dt.second)
        e, r, v = sat.sgp4(jd, fr)
        return v if e == 0 else None
    except Exception: return None

def rel_ms(t1, t2, dt):
    v1 = vel(t1, dt); v2 = vel(t2, dt)
    if v1 is None or v2 is None: return None
    dv = (v1[0]-v2[0], v1[1]-v2[1], v1[2]-v2[2])
    return round(math.sqrt(dv[0]**2+dv[1]**2+dv[2]**2) * 1000.0, 1)

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
    filled = miss_tle = no_tca = sgp4_fail = 0
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
            continue
        rv = rel_ms(t1, t2, tca)
        if rv is None: sgp4_fail += 1; continue
        filled += 1
        if DRYRUN:
            if filled <= 8: log(f"id={rid} {n1}x{n2} rel_v={rv} m/s ({rv/1000:.2f} km/s)")
        else:
            rj["relative_velocity_ms"] = rv; rj["_relvel_src"] = "sgp4"
            cur.execute("UPDATE conjunction_events SET raw_json=%s WHERE id=%s", (Json(rj), rid)); conn.commit()
    cur.close(); conn.close()
    log(f"DONE filled={filled} miss_tle={miss_tle} no_tca={no_tca} sgp4_fail={sgp4_fail}")
    return 0

if __name__ == "__main__":
    try: sys.exit(main())
    except Exception as e:
        log(f"FATAL: {type(e).__name__}: {e}"); sys.exit(1)
