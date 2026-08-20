#!/usr/bin/env python3
"""Refresh TheSpaceDevs launch schedule cache.

Runs on cron (every 4h). The engine's /api/launches route reads these files
and never calls the upstream API during a user request — LL2's free tier
rate-limits at ~15 req/h, so per-request fetching breaks under load.

Fail-soft: if upstream is down, the existing cache file is left untouched.
The engine will serve it with a `stale_hours` marker.
"""
import json, os, ssl, sys, time, urllib.request

_CAS_HOME = os.environ.get("CAS_HOME", "/opt/cas").rstrip("/") or "/opt/cas"
sys.path.insert(0, os.path.join(_CAS_HOME, "cas_api"))
try:
    from core.data_health import report_success as _dh_ok, report_failure as _dh_fail
except Exception as _dh_e:
    print(f"[launch] data_health import failed ({_dh_e}); health disabled")
    def _dh_ok(*a, **k): pass
    def _dh_fail(*a, **k): pass

MODES = {"upcoming": "upcoming", "recent": "previous"}
CACHE = os.path.join(_CAS_HOME, ".launches_cache_{}.json")
API = "https://ll.thespacedevs.com/2.2.0/launch/{}/?limit=20&format=json"
UA = "CAS/1.0 (casplatform.com; conjunction decision support)"


def _ctx():
    c = ssl.create_default_context()
    return c


def shape(raw):
    out = []
    for r in raw.get("results", []):
        mission = r.get("mission") or {}
        pad = r.get("pad") or {}
        lsp = r.get("launch_service_provider") or {}
        status = r.get("status") or {}
        cfg = (r.get("rocket") or {}).get("configuration") or {}
        loc = pad.get("location") or {}
        out.append({
            "id": r.get("id", ""),
            "name": r.get("name", ""),
            "net": r.get("net", ""),
            "window_start": r.get("window_start", ""),
            "window_end": r.get("window_end", ""),
            "status": status.get("abbrev", ""),
            "status_name": status.get("name", ""),
            "provider": lsp.get("name", ""),
            "provider_type": lsp.get("type", ""),
            "rocket": cfg.get("name", r.get("name", "").split("|")[0].strip()),
            "mission_name": mission.get("name", ""),
            "mission_type": mission.get("type", ""),
            "mission_desc": (mission.get("description") or "")[:300],
            "pad_name": pad.get("name", ""),
            "location": loc.get("name", ""),
            "country_code": loc.get("country_code", ""),
            "image": r.get("image", ""),
            "webcast_live": r.get("webcast_live", False),
        })
    return out


def refresh(mode, upstream):
    path = CACHE.format(mode)
    try:
        req = urllib.request.Request(API.format(upstream), headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=20, context=_ctx()) as resp:
            raw = json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        age = ""
        if os.path.exists(path):
            age = f" (keeping cache, {(time.time()-os.path.getmtime(path))/3600:.1f}h old)"
        print(f"  {mode:9} FAILED: {type(e).__name__}: {e}{age}")
        return False

    launches = shape(raw)
    result = {
        "status": "ok",
        "count": len(launches),
        "cached": True,
        "launches": launches,
        "source": "TheSpaceDevs Launch Library 2",
        "refreshed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(result, f)
    os.replace(tmp, path)          # atomic — engine never reads a half-written file
    os.chmod(path, 0o644)
    print(f"  {mode:9} OK — {len(launches)} launches")
    return True


if __name__ == "__main__":
    print(f"=== Launch cache refresh — {time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime())} ===")
    ok = sum(refresh(m, u) for m, u in MODES.items())
    print(f"+ {ok}/{len(MODES)} modes refreshed")
    # ok>0 = at least one mode fetched from TheSpaceDevs = source reachable.
    # ok==0 = every mode failed = source down.
    if ok:
        _dh_ok("launch")
    else:
        _dh_fail("launch", "All launch-schedule fetch modes failed (TheSpaceDevs unreachable)")
    sys.exit(0 if ok else 1)
