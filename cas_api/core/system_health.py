"""System health: the per-component check behind /health/detailed.

Moved out of cas_engine.py on 2026-09-02 under ADR 0001, exception 1. It lives
here rather than being copied because both services now serve it -- the engine
at /health/detailed and /health/sources, FastAPI at /api/v2/health/*. One
implementation means the two endpoints cannot drift apart, which is the failure
notification-prefs demonstrated on the same codebase.

Direction of dependency: the engine imports this module, never the other way
round. That is the property ADR 0001 relies on to keep a future migration cheap.

Component status comes from data_health -- "did our job run" -- and not from the
age of the newest row in a table, which measures how talkative upstream has
been. The reasoning, and the twelve-hours-of-503-a-day bug that produced it, is
in feed_component's docstring below.
"""
import glob
import os
import shutil
import time

import psycopg2

from core.paths import CAS_HOME


def deploy_fields():
    """commit / deployed_at for the health payloads, never raising.

    Came over from the engine with the rest of this module: deploy_info already
    lived in cas_api, so the engine was reaching back here through sys.path for
    it. Now the call is a plain import.
    """
    try:
        from core.deploy_info import deploy_info
        d = deploy_info(CAS_HOME)
        return {"commit": d["commit_short"] or "unknown",
                "deployed_at": d["deployed_at"]}
    except Exception:
        return {"commit": "unknown", "deployed_at": None}


def feed_component(source, extra=None):
    """Status for one ingestion feed, taken from data_health.

    WHY NOT MAX(fetched_at). Each of these components used to time the newest
    ROW in a table and call the age a fault of ours. That measures how talkative
    upstream has been, not whether our pipeline works, and the two came apart in
    both directions:

      * A genuinely quiet upstream drove the component to "error" and the whole
        endpoint to 503, while data_health -- correctly -- said the fetch had
        run and succeeded. Two endpoints, opposite answers, same event.
      * The thresholds were written for a schedule that no longer exists. The
        CDM check assumed an hourly cron ("warn if >90 min ... error if >4h")
        while fetch_cdm.py runs three times a day, because Space-Track allows
        three CDM requests per day and we use all three. Arithmetic: ok for the
        first 90 minutes of each 8-hour cycle, warning for 150, error for the
        remaining 240 -- twelve hours of 503 every day, on a healthy system.
      * EU SST was worse. Measured 2026-08-27 over 12 months of update_date:
        reentries publish with a median gap of 5 days, p95 13, max 15;
        fragmentations median 33 days, max 72. A 48h warning and a 168h error
        threshold flag a feed that is behaving exactly as it always has.

    data_health answers the question a health endpoint should ask -- did our
    job run and succeed -- and it already carries the expected interval per
    source, so the thresholds live in one place next to the cron schedule they
    come from instead of being retyped here as numbers that go stale.

    Row age is still reported, as data_age_hours, because it is worth seeing.
    It just no longer decides the status or the HTTP code.
    """
    out = dict(extra or {})
    try:
        from core.data_health import get_health as _gh
        h = _gh(source)
    except Exception as e:
        out.update({"status": "error", "error": f"health lookup failed: {str(e)[:80]}"})
        return out
    out["last_success"] = h.get("last_success_at")
    out["minutes_since_success"] = h.get("minutes_stale")
    out["consecutive_failures"] = h.get("consecutive_failures", 0)
    # get_health()'s status already folds staleness in ("stale"), so this maps
    # one field instead of second-guessing two. It used to read
    # `status == "failed" or is_stale`, which was correct but meant every
    # consumer had to remember that the status column is a latch.
    out["reported_status"] = h.get("reported_status")
    if h.get("minutes_since_added") is not None:
        out["days_since_added"] = round(h["minutes_since_added"] / 1440.0, 1)
    st = h.get("status")
    if st == "unknown":
        # No row yet, and not yet overdue: the ordinary state between deploying
        # a new source and its first cron run.
        out["status"] = "warning"
        out["message"] = "no health record yet"
    elif st == "never_ran":
        # Two intervals past the day it was added and still nothing. A cron
        # line that was never installed, or a script that cannot start.
        out["status"] = "error"
        out["message"] = "never reported since %s" % (
            out.get("added_on") or "it was added")
    elif st in ("failed", "stale"):
        out["status"] = "error"
    elif st == "degraded":
        out["status"] = "warning"
    else:
        out["status"] = "ok"
    return out


def max_age_hours(table, column):
    """Age of the newest row, in hours. Informational only -- see above."""
    try:
        conn = psycopg2.connect(os.environ.get("DB_URL", ""))
        cur = conn.cursor()
        cur.execute(f"SELECT MAX({column}) FROM {table}")
        row = cur.fetchone()
        cur.close(); conn.close()
        if not row or not row[0]:
            return None
        return round((time.time() - row[0].timestamp()) / 3600, 2)
    except Exception:
        return None


def check_system_health(start_time=None, version=None):
    """Comprehensive health check across all subsystems.
    Returns dict with overall status + per-component details.
    Status levels: 'ok', 'warning', 'error'"""
    components = {}
    checks_passed = 0
    checks_failed = 0

    # === Database connectivity + size ===
    try:
        db_start = time.time()
        conn = psycopg2.connect(os.environ.get("DB_URL", ""))
        cur = conn.cursor()
        cur.execute("SELECT pg_database_size(current_database())")
        db_size_bytes = cur.fetchone()[0]
        cur.close()
        conn.close()
        db_latency_ms = round((time.time() - db_start) * 1000, 1)
        components["database"] = {
            "status": "ok",
            "size_mb": round(db_size_bytes / (1024*1024), 1),
            "latency_ms": db_latency_ms,
        }
        checks_passed += 1
    except Exception as e:
        components["database"] = {"status": "error", "error": str(e)[:100]}
        checks_failed += 1

    # === Space-Track CDM ingestion ===
    try:
        conn = psycopg2.connect(os.environ.get("DB_URL", ""))
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FILTER (WHERE fetched_at > NOW() - INTERVAL '24 hours') FROM conjunction_events")
        last_24h = cur.fetchone()[0]
        cur.close()
        conn.close()
    except Exception:
        last_24h = None
    components["space_track"] = feed_component("cdm", {
        "inserts_24h": last_24h if last_24h is not None else "unknown",
        # Informational: a quiet Space-Track day is normal and fetch_cdm.py says
        # so explicitly ("total=0 is a QUIET day, NOT a failure").
        "data_age_hours": max_age_hours("conjunction_events", "fetched_at"),
    })
    if components["space_track"]["status"] == "ok":
        checks_passed += 1
    else:
        checks_failed += 1

    # === EU SST sync ===
    try:
        conn = psycopg2.connect(os.environ.get("DB_URL", ""))
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM eusst_re_events")
        total = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM eusst_fg_events")
        total_fg = cur.fetchone()[0]
        cur.close()
        conn.close()
    except Exception:
        total = total_fg = None
    components["eu_sst"] = feed_component("eusst", {
        "reentry_events": total if total is not None else "unknown",
        "fragmentation_events": total_fg if total_fg is not None else "unknown",
        # Informational, and deliberately not a threshold: EU SST publishes
        # reentries with a median gap of 5 days (max 15 over 12 months) and
        # fragmentations with a median of 33 days (max 72). Nothing about that
        # cadence is a fault of ours.
        "newest_event_age_hours": max_age_hours("eusst_re_events", "update_date"),
    })
    if components["eu_sst"]["status"] == "ok":
        checks_passed += 1
    else:
        checks_failed += 1

    # === NOAA Space Weather ===
    # Converted with the other two even though its numbers happened to match
    # reality (hourly cron, measured max gap over 7 days: 1.0h). Leaving one
    # feed on a different mechanism is how the next person learns the wrong
    # pattern from the file.
    components["noaa_swpc"] = feed_component("space_weather", {
        "data_age_hours": max_age_hours("space_weather_snapshots", "fetched_at"),
    })
    if components["noaa_swpc"]["status"] == "ok":
        checks_passed += 1
    else:
        checks_failed += 1

    # === Disk usage ===
    try:
        import shutil as shutil
        total, used, free = shutil.disk_usage(CAS_HOME)
        used_pct = round((used / total) * 100, 1)
        free_gb = round(free / (1024**3), 1)
        if used_pct > 90:
            status = "error"
        elif used_pct > 80:
            status = "warning"
        else:
            status = "ok"
        components["disk"] = {
            "status": status,
            "free_gb": free_gb,
            "used_pct": used_pct,
        }
        if status == "ok":
            checks_passed += 1
        else:
            checks_failed += 1
    except Exception as e:
        components["disk"] = {"status": "error", "error": str(e)[:100]}
        checks_failed += 1

    # === Backup freshness ===
    try:
        import glob as glob
        backup_files = sorted(glob.glob(
            os.path.join(CAS_HOME, "backups/db/daily/*.sql.gz")), reverse=True)
        if backup_files:
            latest = backup_files[0]
            age_seconds = time.time() - os.path.getmtime(latest)
            age_hours = round(age_seconds / 3600, 1)
            # warn if >26h, error if >48h
            if age_hours > 48:
                status = "error"
            elif age_hours > 26:
                status = "warning"
            else:
                status = "ok"
            components["backup"] = {
                "status": status,
                "last_backup_age_hours": age_hours,
                "daily_count": len(backup_files),
                "latest_size_kb": round(os.path.getsize(latest) / 1024, 1),
            }
            if status == "ok":
                checks_passed += 1
            else:
                checks_failed += 1
        else:
            components["backup"] = {"status": "warning", "message": "no backups yet"}
            checks_failed += 1
    except Exception as e:
        components["backup"] = {"status": "error", "error": str(e)[:100]}
        checks_failed += 1

    # === Overall status ===
    statuses = [c.get("status", "error") for c in components.values()]
    if "error" in statuses:
        overall = "error"
    elif "warning" in statuses:
        overall = "warning"
    else:
        overall = "ok"

    uptime_seconds = int(time.time() - start_time) if start_time else None

    return {
        "status": overall,
        "version": version or "0.7",
        **deploy_fields(),
        "uptime_seconds": uptime_seconds,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "components": components,
        "checks_passed": checks_passed,
        "checks_failed": checks_failed,
    }
