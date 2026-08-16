#!/usr/bin/env python3
"""Refresh Space-Track LEO debris + rocket body catalog cache."""
import http.cookiejar, urllib.request, urllib.parse, json, ssl, os, time, sys

# Central data-health tracking
sys.path.insert(0, "/opt/cas/cas_api")
try:
    from core.data_health import report_success, report_failure
except Exception as _dh_e:
    print(f"[catalog] data_health import failed ({_dh_e}); health disabled")
    def report_success(*a, **k): pass
    def report_failure(*a, **k): pass

ENV = {}
with open("/opt/cas/.env") as f:
    for line in f:
        if "=" in line and not line.startswith("#"):
            k, v = line.strip().split("=", 1)
            ENV[k] = v.strip().strip('"').strip("'")

ctx = ssl.create_default_context()

opener = urllib.request.build_opener(
    urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()),
    urllib.request.HTTPSHandler(context=ctx)
)

login_data = urllib.parse.urlencode({
    "identity": ENV.get("ST_IDENTITY",""),
    "password": ENV.get("ST_PASSWORD","")
}).encode()

try:
    opener.open("https://www.space-track.org/ajaxauth/login", login_data, timeout=30)
    print("[OK] Space-Track login")
except Exception as e:
    print(f"[ERROR] Login failed: {e}")
    exit(1)

cache = {"debris": [], "rocket_body": [], "payload": [], "unknown": [], "fetched_at": time.time()}

for obj_type, key in [("DEBRIS", "debris"), ("ROCKET BODY", "rocket_body"), ("PAYLOAD", "payload"), ("UNKNOWN", "unknown")]:
    url = f"https://www.space-track.org/basicspacedata/query/class/gp/OBJECT_TYPE/{urllib.parse.quote(obj_type)}/PERIAPSIS/%3C2000/DECAY_DATE/null-val/predicates/NORAD_CAT_ID,TLE_LINE1,TLE_LINE2/orderby/NORAD_CAT_ID%20asc/format/json/emptyresult/show"
    try:
        resp = opener.open(url, timeout=120)
        raw = resp.read().decode("utf-8")
        # GP JSON + predicates (2026-08-16): the TLE text format encodes catalog
        # numbers >= 100000 as Alpha-5 (100175 -> "A0175"), so slicing cols 3-7
        # produced unusable keys for ~700 objects. JSON returns NORAD_CAT_ID as a
        # plain integer with no 339,999 ceiling, and is smaller on the wire.
        arr = json.loads(raw)
        entries = []
        for _o in arr:
            _n  = str(_o.get("NORAD_CAT_ID") or "").strip()
            # Canonical key: Space-Track has returned both "11" and "00011"
            # for the same object on different days (observed 2026-08-16).
            if _n.isdigit(): _n = str(int(_n))
            _l1 = (_o.get("TLE_LINE1") or "").strip()
            _l2 = (_o.get("TLE_LINE2") or "").strip()
            if _n and _l1 and _l2:
                entries.append({"norad": _n, "l1": _l1, "l2": _l2})
        cache[key] = entries
        print(f"[OK] {obj_type}: {len(entries)} objects")
    except Exception as e:
        print(f"[ERROR] {obj_type}: {e}")

# GUARD: never overwrite a good cache with an empty result. If every query
# came back with zero objects (auth failure, suspension, outage), the existing
# cache is strictly better than nothing -- leave it untouched and fail loudly.
_total = sum(len(cache[k]) for k in ("debris", "rocket_body", "payload", "unknown"))
if _total == 0:
    print("[SKIP] All queries returned 0 objects -- existing cache left untouched")
    report_failure("catalog", "Space-Track returned 0 objects (auth failure, suspension, or outage)")
    raise SystemExit(1)

with open("/opt/cas/.spacetrack_catalog_cache.json", "w") as f:
    json.dump(cache, f)
print(f"[OK] Cache saved: {len(cache['debris'])} debris + {len(cache['rocket_body'])} RB + {len(cache['payload'])} payload + {len(cache['unknown'])} unknown")
report_success("catalog")
