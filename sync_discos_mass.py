#!/usr/bin/env python3
"""Fill physical properties from ESA DISCOS.

SATCAT gives identity; DISCOS gives actual mass and geometry. Mass matters
because cascade modelling is driven by it: the NASA breakup model derives
fragment counts from colliding mass, so a fixed 260 kg assumption is wrong by
orders of magnitude (Envisat 8.1 t, a cubesat 4 kg).

Rate limit: 100 requests/minute, 100 objects/page.
Cron:  30 6 * * 0   cd /opt/cas && python3 sync_discos_mass.py
"""
import json, os, time, urllib.error, urllib.parse, urllib.request
from datetime import datetime, timezone
import psycopg2, psycopg2.extras
import sys
sys.path.insert(0, "/opt/cas/cas_api")
try:
    from core.data_health import report_success as _dh_ok, report_failure as _dh_fail
except Exception as _dh_e:
    print(f"[discos] data_health import failed ({_dh_e}); health disabled")
    def _dh_ok(*a, **k): pass
    def _dh_fail(*a, **k): pass

for line in open("/opt/cas/.env"):
    if "=" in line and not line.strip().startswith("#"):
        k, v = line.strip().split("=", 1)
        os.environ.setdefault(k, v.strip().strip(chr(34)).strip(chr(39)))

BASE = "https://discosweb.esoc.esa.int/api/objects"
HEADERS = {"Authorization": "Bearer " + os.environ.get("DISCOS_TOKEN", ""),
           "DiscosWeb-Api-Version": "2"}
PAGE_SIZE = 100
PER_MINUTE = 90

def log(m):
    print("[" + datetime.now(timezone.utc).strftime("%H:%M:%S") + "] " + str(m), flush=True)

_req = []
def _throttle():
    now = time.time()
    _req[:] = [t for t in _req if now - t < 60]
    if len(_req) >= PER_MINUTE:
        wait = 60 - (now - _req[0]) + 1
        if wait > 0:
            log("  rate limit - waiting %.0fs" % wait)
            time.sleep(wait)
        _req[:] = [t for t in _req if time.time() - t < 60]
    _req.append(time.time())

def fetch_page(page):
    _throttle()
    url = BASE + "?" + urllib.parse.urlencode(
        {"page[size]": PAGE_SIZE, "page[number]": page, "sort": "satno"})
    for attempt in range(4):
        try:
            r = urllib.request.urlopen(urllib.request.Request(url, headers=HEADERS), timeout=120)
            return json.loads(r.read())
        except urllib.error.HTTPError as e:
            if e.code == 429:
                log("  429 - backing off 30s"); time.sleep(30); continue
            log("  page %s: HTTP %s" % (page, e.code)); return None
        except Exception as e:
            log("  page %s: %s (retry %d)" % (page, repr(e)[:80], attempt + 1))
            time.sleep(5)
    return None

def _f(v):
    try: return float(v)
    except (TypeError, ValueError): return None

def main():
    if not os.environ.get("DISCOS_TOKEN"):
        log("DISCOS_TOKEN not set - aborting"); return
    conn = psycopg2.connect(os.environ["DB_URL"]); cur = conn.cursor()

    first = fetch_page(1)
    if not first:
        log("could not read first page - aborting"); return
    total_pages = (first.get("meta", {}) or {}).get("pagination", {}).get("totalPages", 0)
    log("DISCOS: %s pages of %s" % (total_pages, PAGE_SIZE))

    with_mass = 0
    for page in range(1, total_pages + 1):
        doc = first if page == 1 else fetch_page(page)
        if not doc: continue
        rows = []
        for o in doc.get("data", []):
            a = o.get("attributes", {}) or {}
            satno = a.get("satno")
            if satno is None: continue
            mass = _f(a.get("mass"))
            if mass: with_mass += 1
            rows.append((json.dumps([int(satno), mass,
                         "discos" if mass else None,
                         _f(a.get("xSectAvg")), a.get("shape"),
                         _f(a.get("height")), _f(a.get("width")),
                         _f(a.get("depth")), _f(a.get("diameter"))]),))
        if not rows: continue
        psycopg2.extras.execute_values(cur, """
            UPDATE satcat_objects s SET
              mass_kg=COALESCE(v.mass,s.mass_kg),
              mass_source=COALESCE(v.src,s.mass_source),
              xsect_m2=COALESCE(v.xsect,s.xsect_m2),
              shape=COALESCE(v.shape,s.shape),
              height_m=COALESCE(v.h,s.height_m),
              width_m=COALESCE(v.w,s.width_m),
              depth_m=COALESCE(v.d,s.depth_m),
              diameter_m=COALESCE(v.dia,s.diameter_m),
              updated_at=now()
            FROM (SELECT (t->>0)::int AS norad, (t->>1)::real AS mass,
                         (t->>2)::text AS src, (t->>3)::real AS xsect,
                         (t->>4)::text AS shape, (t->>5)::real AS h,
                         (t->>6)::real AS w, (t->>7)::real AS d,
                         (t->>8)::real AS dia
                  FROM (VALUES %s) AS x(t0), LATERAL (SELECT x.t0::jsonb AS t) j) AS v
            WHERE s.norad = v.norad
        """, rows, page_size=200)
        conn.commit()
        if page % 25 == 0 or page == total_pages:
            log("  page %d/%s - %d objects with mass so far" % (page, total_pages, with_mass))

    cur.execute("SELECT count(*) FILTER (WHERE mass_kg IS NOT NULL), "
                "count(*) FILTER (WHERE mass_source='discos'), count(*) FROM satcat_objects")
    m, dm, tot = cur.fetchone()
    log("DONE. %s catalogued | %s with mass (%s from DISCOS)" % (tot, m, dm))
    cur.close(); conn.close()

if __name__ == "__main__":
    try:
        main()
        _dh_ok("discos")
    except SystemExit as _e:
        if _e.code not in (0, None):
            _dh_fail("discos", "DISCOS sync exited with error (token/config)")
        raise
    except Exception as _e:
        _dh_fail("discos", f"{type(_e).__name__}: {_e}")
        raise
