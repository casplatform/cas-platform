#!/usr/bin/env python3
"""
CAS — Business Directory satellite-count sync from Space-Track.

For each curated directory entry that has a `constellation` value, queries
Space-Track GP for the current on-orbit (non-decayed) payload count matching
the constellation name pattern, and updates satellite_count + updated_at.

This gives the directory a live, periodically-refreshed satellite count
(e.g. Starlink grows week to week) while keeping curated metadata intact.
Constellation -> name pattern mapping is explicit (no guessing).
"""
import http.cookiejar, urllib.request, urllib.parse, json, ssl, os, time, sys
import psycopg2

# data_health is optional at import: a checkout has no cas_api on sys.path, and
# this module is imported by the test suite.
try:
    sys.path.insert(0, os.path.join(
        os.environ.get("CAS_HOME", "/opt/cas").rstrip("/") or "/opt/cas", "cas_api"))
    from core.data_health import report_success as _dh_ok, report_failure as _dh_fail
except Exception as _dh_e:
    print(f"[sync] data_health unavailable ({_dh_e}); health reporting disabled")
    def _dh_ok(*a, **k): pass
    def _dh_fail(*a, **k): pass

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


DB_URL = _dsn()

# Load env
ENV = {}
with open(os.path.join(_CAS_HOME, ".env")) as f:
    for line in f:
        if "=" in line and not line.startswith("#"):
            k, v = line.strip().split("=", 1)
            ENV[k] = v.strip().strip('"').strip("'")

# Explicit constellation -> Space-Track OBJECT_NAME LIKE pattern
# Only entries we can reliably match by name pattern. Others keep curated count.
CONSTELLATION_PATTERNS = {
    "Starlink":      "STARLINK~~",
    "OneWeb":        "ONEWEB~~",
    "Kuiper":        "KUIPER~~",
    "Iridium NEXT":  "IRIDIUM~~",
    "Globalstar":    "GLOBALSTAR~~",
    "Flock/SkySat":  "FLOCK~~",       # Planet — partial (SkySat separate naming)
    "LEMUR":         "LEMUR~~",       # Spire
    "ICEYE SAR":     "ICEYE~~",
    "Qianfan/G60":   "QIANFAN~~",
    "O3b mPOWER":    "O3B~~",         # SES
    "Orbcomm":       "ORBCOMM~~",
    "ION":           "ION%20SCV~~",  # D-Orbit (space URL-encoded)
    # "NewSat" (Satellogic) removed — NUSAT/ÑuSat naming unreliable, keep curated count
    "HawkEye":       "HAWK~~",
}

def st_login():
    ctx = ssl.create_default_context()
    opener = urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()),
        urllib.request.HTTPSHandler(context=ctx)
    )
    login_data = urllib.parse.urlencode({
        "identity": ENV.get("ST_IDENTITY",""),
        "password": ENV.get("ST_PASSWORD","")
    }).encode()
    opener.open("https://www.space-track.org/ajaxauth/login", login_data, timeout=30)
    return opener

def count_constellation(opener, pattern):
    """Query GP for current non-decayed payload count matching name pattern."""
    # OBJECT_NAME LIKE pattern, PAYLOAD only, not decayed
    q = (f"https://www.space-track.org/basicspacedata/query/class/gp/"
         f"OBJECT_TYPE/PAYLOAD/OBJECT_NAME/{pattern}/DECAY_DATE/null-val/"
         f"predicates/NORAD_CAT_ID/format/json")
    try:
        resp = opener.open(q, timeout=90)
        data = json.loads(resp.read().decode("utf-8"))
        return len(data)
    except Exception as e:
        print(f"  [WARN] query failed for {pattern}: {e}")
        return None

def main():
    print("[sync] Logging in to Space-Track...")
    try:
        opener = st_login()
        print("[sync] Login OK")
    except Exception as e:
        print(f"[ERROR] Login failed: {e}")
        sys.exit(1)

    conn = psycopg2.connect(DB_URL)
    cur = conn.cursor()

    # Get curated entries with a constellation we can match
    cur.execute("""
        SELECT id, name, constellation, satellite_count
        FROM business_directory
        WHERE constellation IS NOT NULL AND constellation != ''
    """)
    rows = cur.fetchall()
    print(f"[sync] {len(rows)} entries with constellation")

    updated = 0
    # Counted separately from `updated` on purpose. A week in which no count
    # changed is a normal quiet week; a week in which no constellation MATCHED
    # is Space-Track refusing us. Only the second is a failure, and `updated`
    # alone cannot tell them apart.
    matched = 0
    attempted = 0
    for entry_id, name, constellation, old_count in rows:
        pattern = CONSTELLATION_PATTERNS.get(constellation)
        if not pattern:
            continue  # no reliable pattern — keep curated count
        attempted += 1
        time.sleep(1.5)  # rate-limit politeness
        cnt = count_constellation(opener, pattern)
        if cnt is not None and cnt > 0:
            matched += 1
        if cnt is None or cnt == 0:
            print(f"  {name} ({constellation}): no match, keeping {old_count}")
            continue
        cur.execute("""
            UPDATE business_directory
            SET satellite_count = %s, updated_at = NOW(), data_source = 'spacetrack_sync'
            WHERE id = %s
        """, (cnt, entry_id))
        print(f"  {name} ({constellation}): {old_count} -> {cnt}")
        updated += 1

    conn.commit()
    cur.close()
    conn.close()
    print(f"[sync] DONE — {updated} entries updated, {matched}/{attempted} matched")

    if attempted and matched == 0:
        _dh_fail("directory_satcat",
                 "queried %d constellations and matched none -- Space-Track "
                 "returned nothing usable; curated counts left untouched" % attempted)
    else:
        _dh_ok("directory_satcat")

if __name__ == "__main__":
    try:
        main()
    except SystemExit as _e:
        # sys.exit(1) above is the login failure path, which never reaches the
        # reporting at the end of main().
        if _e.code not in (0, None):
            _dh_fail("directory_satcat", "sync exited %s (Space-Track login or config)" % _e.code)
        raise
    except Exception as _e:
        _dh_fail("directory_satcat", f"{type(_e).__name__}: {_e}")
        raise
