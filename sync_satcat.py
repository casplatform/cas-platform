#!/usr/bin/env python3
"""Sync object identity from the Space-Track SATCAT.

Gives every catalogued object a name, a radar-cross-section class and launch
metadata. Mass is left empty here: SATCAT does not carry it, and an RCS class
is a size proxy, not a mass. When a DISCOS token is available, mass_kg is
filled from there and mass_source records which it came from.

Cron:  0 6 * * 0   cd /opt/cas && python3 sync_satcat.py
"""
import http.cookiejar
import json
import os
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone

import psycopg2
import psycopg2.extras
sys.path.insert(0, "/opt/cas/cas_api")
try:
    from core.data_health import report_success as _dh_ok, report_failure as _dh_fail
except Exception as _dh_e:
    print(f"[satcat] data_health import failed ({_dh_e}); health disabled")
    def _dh_ok(*a, **k): pass
    def _dh_fail(*a, **k): pass

for line in open("/opt/cas/.env"):
    if "=" in line and not line.strip().startswith("#"):
        k, v = line.strip().split("=", 1)
        os.environ.setdefault(k, v.strip().strip('"').strip("'"))

ST = "https://www.space-track.org"
PAGE = 5000


def log(m):
    print(f"[{datetime.now(timezone.utc):%H:%M:%S}] {m}", flush=True)


def login():
    cj = http.cookiejar.CookieJar()
    op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
    op.open(f"{ST}/ajaxauth/login",
            urllib.parse.urlencode({"identity": os.environ["ST_IDENTITY"],
                                    "password": os.environ["ST_PASSWORD"]}).encode(),
            timeout=30)
    return op


def _date(v):
    if not v or v in ("", "null"):
        return None
    try:
        return datetime.strptime(v[:10], "%Y-%m-%d").date()
    except Exception:
        return None


def _num(v, cast=float):
    try:
        return cast(v)
    except (TypeError, ValueError):
        return None


def main():
    op = login()
    log("Space-Track login OK")
    conn = psycopg2.connect(os.environ["DB_URL"])
    cur = conn.cursor()

    total = 0
    offset = 0
    while True:
        q = (f"{ST}/basicspacedata/query/class/satcat/CURRENT/Y"
             f"/orderby/NORAD_CAT_ID%20asc/limit/{PAGE},{offset}/format/json")
        try:
            recs = json.loads(op.open(q, timeout=180).read())
        except Exception as e:
            log(f"query failed at offset {offset}: {e}")
            time.sleep(20)
            op = login()
            continue
        if not recs:
            break

        rows = []
        for r in recs:
            n = _num(r.get("NORAD_CAT_ID"), int)
            if n is None:
                continue
            rows.append((
                n,
                (r.get("SATNAME") or r.get("OBJECT_NAME") or "").strip() or None,
                r.get("OBJECT_TYPE"),
                r.get("RCS_SIZE"),
                _num(r.get("RCSVALUE")),
                r.get("COUNTRY"),
                _date(r.get("LAUNCH")),
                _date(r.get("DECAY")),
                _num(r.get("APOGEE"), int),
                _num(r.get("PERIGEE"), int),
                _num(r.get("INCLINATION")),
            ))

        psycopg2.extras.execute_values(cur, """
            INSERT INTO satcat_objects
              (norad,name,object_type,rcs_size,rcs_value,country,
               launch_date,decay_date,apogee_km,perigee_km,inclination)
            VALUES %s
            ON CONFLICT (norad) DO UPDATE SET
              name=EXCLUDED.name, object_type=EXCLUDED.object_type,
              rcs_size=EXCLUDED.rcs_size, rcs_value=EXCLUDED.rcs_value,
              country=EXCLUDED.country, launch_date=EXCLUDED.launch_date,
              decay_date=EXCLUDED.decay_date, apogee_km=EXCLUDED.apogee_km,
              perigee_km=EXCLUDED.perigee_km, inclination=EXCLUDED.inclination,
              updated_at=now()
        """, rows, page_size=1000)
        conn.commit()
        total += len(rows)
        offset += PAGE
        log(f"  {total:,} objects synced")
        if len(recs) < PAGE:
            break
        time.sleep(2)

    cur.execute("SELECT count(*), count(name), count(rcs_size) FROM satcat_objects")
    n, named, rcs = cur.fetchone()
    log(f"DONE. {n:,} objects | {named:,} named | {rcs:,} with RCS class")
    cur.close()
    conn.close()


if __name__ == "__main__":
    try:
        main()
        _dh_ok("satcat")
    except SystemExit as _e:
        if _e.code not in (0, None):
            _dh_fail("satcat", "SATCAT sync exited with error (Space-Track access)")
        raise
    except Exception as _e:
        _dh_fail("satcat", f"{type(_e).__name__}: {_e}")
        raise
