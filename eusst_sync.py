#!/usr/bin/env python3
"""
CAS Platform — EU SST Sync Script
==================================
Fetches Fragmentation (FG) and Re-entry (RE) events from EU SST
Service Provision Portal API and upserts them into PostgreSQL.

Usage:
    python3 eusst_sync.py --service all              # default: incremental
    python3 eusst_sync.py --service fg --full        # full re-sync FG
    python3 eusst_sync.py --service re               # incremental RE only
    python3 eusst_sync.py --service all --dry-run    # no DB writes

Cron suggestion (every 6 hours):
    0 */6 * * * /usr/bin/python3 /opt/cas/eusst_sync.py --service all >> /var/log/cas/eusst_sync.log 2>&1
"""

import argparse
import json
import os
import sys
import time
import urllib.parse
import urllib.request
import urllib.error
from datetime import datetime, timezone
from pathlib import Path

import psycopg2
import psycopg2.extras

# Central data-health tracking. Path from CAS_HOME so a staging run reports
# into staging's data_health, not production's.
_CAS_HOME = os.environ.get("CAS_HOME", "/opt/cas").rstrip("/") or "/opt/cas"
_CAS_API_HOME = os.path.join(_CAS_HOME, "cas_api")
if _CAS_API_HOME not in sys.path:
    sys.path.insert(0, _CAS_API_HOME)
try:
    from core.data_health import report_success as _dh_ok, report_failure as _dh_fail
except Exception as _dh_e:
    print(f"[eusst] data_health import failed ({_dh_e}); health disabled")
    def _dh_ok(*a, **k): pass
    def _dh_fail(*a, **k): pass

ENV_PATH = Path(_CAS_HOME) / ".env"
PAGE_SIZE = 500  # API max
REQUEST_TIMEOUT = 30


# ----------------------------------------------------------------
# Config & DB
# ----------------------------------------------------------------
def load_env():
    env = {}
    for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def db_connect(env):
    db_url = env.get("DB_URL") or env.get("DATABASE_URL")
    if db_url:
        return psycopg2.connect(db_url)
    return psycopg2.connect(
        host=env.get("DB_HOST", "localhost"),
        port=env.get("DB_PORT", "5432"),
        dbname=env.get("DB_NAME", "casdb"),
        user=env.get("DB_USER", "cas"),
        password=env.get("DB_PASSWORD", ""),
    )


# ----------------------------------------------------------------
# HTTP helpers
# ----------------------------------------------------------------
def http_post_form(url, data):
    body = urllib.parse.urlencode(data).encode("utf-8")
    req = urllib.request.Request(
        url, data=body, method="POST",
        headers={
            "Accept": "application/json",
            "Content-Type": "application/x-www-form-urlencoded",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def http_get_json(base_url, path, params, token):
    query = urllib.parse.urlencode(params, safe='')
    url = f"{base_url}{path}?{query}"
    req = urllib.request.Request(
        url, method="GET",
        headers={
            "Accept": "application/json",
            "Authorization": f"Bearer {token}",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return e.code, {"error": e.read().decode("utf-8", errors="replace")}


# ----------------------------------------------------------------
# Token management
# ----------------------------------------------------------------
class TokenManager:
    def __init__(self, env):
        self.env = env
        self.token = None
        self.expires_at = 0

    def get(self):
        now = time.time()
        if self.token and now < self.expires_at - 30:
            return self.token
        print(f"[token] Requesting new access token...")
        status, body = http_post_form(
            self.env["EUSST_TOKEN_URL"],
            {
                "grant_type": "password",
                "scope": "sstservices",
                "response_type": "token",
                "client_id": self.env["EUSST_CLIENT_ID"],
                "client_secret": self.env["EUSST_CLIENT_SECRET"],
                "username": self.env["EUSST_USERNAME"],
                "password": self.env["EUSST_PASSWORD"],
            },
        )
        if status != 200:
            raise RuntimeError(f"Token request failed: HTTP {status} — {body[:300]}")
        data = json.loads(body)
        self.token = data["access_token"]
        self.expires_at = now + int(data.get("expires_in", 360))
        print(f"[token] OK (expires in {data.get('expires_in')}s)")
        return self.token


# ----------------------------------------------------------------
# Fetch logic
# ----------------------------------------------------------------
def fetch_all_events(env, token_mgr, service, since=None):
    """Fetch all events, paginated. service = 'fg' or 're'."""
    path = f"/api/{service}/event"
    all_items = []
    skip = 0
    while True:
        params = {"$top": str(PAGE_SIZE), "$skip": str(skip)}
        if since:
            ts = since.strftime("%Y-%m-%dT%H:%M:%S.000Z")
            params["$filter"] = f"updateDate gt {ts}"
        token = token_mgr.get()
        status, data = http_get_json(env["EUSST_API_BASE"], path, params, token)
        if status != 200:
            raise RuntimeError(f"Fetch failed ({service}): HTTP {status} — {data}")
        items = data.get("value", data) if isinstance(data, dict) else data
        if not isinstance(items, list):
            items = []
        all_items.extend(items)
        print(f"[{service}] Fetched {len(items)} (total {len(all_items)})")
        if len(items) < PAGE_SIZE:
            break
        skip += PAGE_SIZE
    return all_items


# ----------------------------------------------------------------
# Field mapping
# ----------------------------------------------------------------
def parse_ts(value):
    """Parse EU SST timestamp; return None if invalid."""
    if not value:
        return None
    try:
        # Handle both with and without 'Z'
        if value.endswith("Z"):
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        # Some timestamps come without TZ — assume UTC
        dt = datetime.fromisoformat(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (ValueError, TypeError):
        return None


def map_fg_event(ev):
    lr = ev.get("lastReport") or {}
    return {
        "event_id": ev.get("eventId"),
        "eusst_internal_id": ev.get("id"),
        "creation_date": parse_ts(ev.get("creationDate")),
        "update_date": parse_ts(ev.get("updateDate")),
        "publish_date": parse_ts(lr.get("publishDate")),
        "event_epoch": parse_ts(lr.get("eventEpoch")),
        "originator": lr.get("originator"),
        "product_id": lr.get("productID"),
        "total_reports": ev.get("totalReports") or 0,
        "parent1_object_name": lr.get("parent1ObjectName"),
        "parent1_intl_designator": lr.get("parent1InternationalDesignator"),
        "parent1_norad_id": str(lr.get("parent1NoradId")) if lr.get("parent1NoradId") else None,
        "parent1_object_type": lr.get("parent1ObjectType"),
        "parent1_object_size": lr.get("parent1ObjectSize"),
        "parent1_apogee_km": lr.get("parent1Apogee"),
        "parent1_perigee_km": lr.get("parent1Perigee"),
        "parent2_object_name": lr.get("parent2ObjectName"),
        "parent2_intl_designator": lr.get("parent2InternationalDesignator"),
        "parent2_norad_id": str(lr.get("parent2NoradId")) if lr.get("parent2NoradId") else None,
        "parent2_object_type": lr.get("parent2ObjectType"),
        "parent2_object_size": lr.get("parent2ObjectSize"),
        "parent2_apogee_km": lr.get("parent2Apogee"),
        "parent2_perigee_km": lr.get("parent2Perigee"),
        "frags_detected": lr.get("fragsDetected"),
        "autonomous": lr.get("autonomous"),
        "orbit_regime": lr.get("orbitRegime"),
        "fragmentation_type": lr.get("fragmentationType"),
        "download_link": lr.get("downloadLink"),
        "file_name": lr.get("fileName"),
        "raw_payload": json.dumps(ev),
    }


def map_re_event(ev):
    lr = ev.get("lastReport") or {}
    def _num(v):
        try: return float(v) if v is not None else None
        except (TypeError, ValueError): return None
    return {
        "event_id": ev.get("eventId"),
        "eusst_internal_id": ev.get("id"),
        "creation_date": parse_ts(ev.get("creationDate")),
        "update_date": parse_ts(ev.get("updateDate")),
        "publish_date": parse_ts(lr.get("publishDate")),
        "total_reports": ev.get("totalReports") or 0,
        "object_name": lr.get("objectName") or lr.get("spaceObjectName"),
        "intl_designator": lr.get("internationalDesignator"),
        "norad_id": str(lr.get("noradId")) if lr.get("noradId") else None,
        "object_type": lr.get("objectType"),
        "object_size": lr.get("objectSize") if isinstance(lr.get("objectSize"), str) else (str(lr.get("objectSize")) if lr.get("objectSize") is not None else None),
        "reentry_start_window": parse_ts(lr.get("windowStart") or lr.get("startWindow") or lr.get("reentryStartWindow")),
        "reentry_end_window": parse_ts(lr.get("windowEnd") or lr.get("endWindow") or lr.get("reentryEndWindow")),
        "reentry_tca": parse_ts(lr.get("reentryEpoch") or lr.get("reentryTime") or lr.get("nominalReentryTime")),
        "inclination_deg": _num(lr.get("inclination")),
        "apogee_km": _num(lr.get("apogee")),
        "perigee_km": _num(lr.get("perigee")),
        "reentry_altitude": str(lr.get("reentryAltitude")) if lr.get("reentryAltitude") is not None else None,
        "decay": str(lr.get("decay")) if lr.get("decay") is not None else None,
        "autonomous": str(lr.get("autonomous")) if lr.get("autonomous") is not None else None,
        "risk_level": str(lr.get("riskLevel")) if lr.get("riskLevel") is not None else None,
        "risk_level_comment": lr.get("riskLevelComment"),
        "max_latitude": _num(lr.get("maxLatitude")),
        "aoi_list": json.dumps(lr.get("areasOfInterest") or lr.get("aoiList")) if (lr.get("areasOfInterest") or lr.get("aoiList")) is not None else None,
        "download_link": lr.get("downloadLink"),
        "file_name": lr.get("fileName"),
        "raw_payload": json.dumps(ev),
    }



# ----------------------------------------------------------------
# Upsert
# ----------------------------------------------------------------
FG_COLS = [
    "event_id", "eusst_internal_id", "creation_date", "update_date", "publish_date",
    "event_epoch", "originator", "product_id", "total_reports",
    "parent1_object_name", "parent1_intl_designator", "parent1_norad_id",
    "parent1_object_type", "parent1_object_size", "parent1_apogee_km", "parent1_perigee_km",
    "parent2_object_name", "parent2_intl_designator", "parent2_norad_id",
    "parent2_object_type", "parent2_object_size", "parent2_apogee_km", "parent2_perigee_km",
    "frags_detected", "autonomous", "orbit_regime", "fragmentation_type",
    "download_link", "file_name", "raw_payload",
]

RE_COLS = [
    "event_id", "eusst_internal_id", "creation_date", "update_date", "publish_date",
    "total_reports", "object_name", "intl_designator", "norad_id", "object_type",
    "object_size", "reentry_start_window", "reentry_end_window", "reentry_tca",
    "inclination_deg", "apogee_km", "perigee_km",
    "reentry_altitude", "decay", "autonomous",
    "risk_level", "risk_level_comment", "max_latitude",
    "aoi_list", "download_link", "file_name", "raw_payload",
]


def upsert_events(conn, table, cols, rows, dry_run=False):
    if not rows:
        return 0
    placeholders = ",".join(["%s"] * len(cols))
    col_list = ",".join(cols)
    update_clause = ",".join([f"{c}=EXCLUDED.{c}" for c in cols if c != "event_id"])
    sql = f"""
        INSERT INTO {table} ({col_list}, last_seen_at)
        VALUES ({placeholders}, NOW())
        ON CONFLICT (event_id) DO UPDATE SET
            {update_clause},
            last_seen_at = NOW()
    """
    if dry_run:
        print(f"[dry-run] Would upsert {len(rows)} rows into {table}")
        return len(rows)
    with conn.cursor() as cur:
        for row in rows:
            cur.execute(sql, [row[c] for c in cols] )
    conn.commit()
    return len(rows)


# ----------------------------------------------------------------
# Sync state
# ----------------------------------------------------------------
def get_sync_state(conn, service):
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("SELECT * FROM eusst_sync_state WHERE service=%s", (service,))
        return cur.fetchone()


def update_sync_state(conn, service, status, error=None, total=None, max_update=None):
    with conn.cursor() as cur:
        cur.execute("""
            UPDATE eusst_sync_state
            SET last_sync_at = NOW(),
                last_status = %s,
                last_error = %s,
                events_total = COALESCE(%s, events_total),
                last_update_date = COALESCE(%s, last_update_date)
            WHERE service = %s
        """, (status, error, total, max_update, service))
    conn.commit()


# ----------------------------------------------------------------
# Main sync routine
# ----------------------------------------------------------------
def sync_service(env, conn, token_mgr, service, full=False, dry_run=False, limit=None):
    print(f"\n=== Syncing {service.upper()} ===")
    state = get_sync_state(conn, service) if not dry_run else None
    since = None
    if not full and state and state.get("last_update_date"):
        since = state["last_update_date"]
        print(f"[{service}] Incremental since {since}")
    else:
        print(f"[{service}] FULL sync")

    try:
        events = fetch_all_events(env, token_mgr, service, since=since)
    except Exception as e:
        if not dry_run:
            update_sync_state(conn, service, "error", error=str(e)[:500])
        print(f"[{service}] FETCH ERROR: {e}")
        return

    print(f"[{service}] Total fetched: {len(events)}")
    if not events:
        if not dry_run:
            # Don't reset events_total — just update sync time and status
            update_sync_state(conn, service, "ok")
        return

    # Map
    if service == "fg":
        rows = [map_fg_event(e) for e in events]
        if limit: rows = rows[:limit]
        table, cols = "eusst_fg_events", FG_COLS
    else:
        rows = [map_re_event(e) for e in events]
        if limit: rows = rows[:limit]
        table, cols = "eusst_re_events", RE_COLS

    # Filter out events without event_id
    rows = [r for r in rows if r.get("event_id")]
    print(f"[{service}] Mapped {len(rows)} valid rows")

    # Find max updateDate for next incremental
    max_update = None
    for r in rows:
        ud = r.get("update_date")
        if ud and (max_update is None or ud > max_update):
            max_update = ud

    # Upsert
    n = upsert_events(conn, table, cols, rows, dry_run=dry_run)
    print(f"[{service}] Upserted {n} rows into {table}")

    if not dry_run:
        with conn.cursor() as cur:
            cur.execute(f"SELECT COUNT(*) FROM {table}")
            total = cur.fetchone()[0]
        update_sync_state(conn, service, "ok", total=total, max_update=max_update)
        print(f"[{service}] DB total now: {total}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--service", choices=["fg", "re", "all"], default="all")
    ap.add_argument("--full", action="store_true", help="Full re-sync (ignore last_update_date)")
    ap.add_argument("--limit", type=int, default=None, help="Max events per service (test mode)")
    ap.add_argument("--dry-run", action="store_true", help="Don't write to DB")
    args = ap.parse_args()

    env = load_env()
    for k in ("EUSST_CLIENT_ID", "EUSST_CLIENT_SECRET", "EUSST_USERNAME",
              "EUSST_PASSWORD", "EUSST_TOKEN_URL", "EUSST_API_BASE"):
        if not env.get(k):
            print(f"ERROR: missing {k} in .env")
            sys.exit(1)

    conn = db_connect(env)
    token_mgr = TokenManager(env)

    services = ["fg", "re"] if args.service == "all" else [args.service]
    for s in services:
        sync_service(env, conn, token_mgr, s, full=args.full, dry_run=args.dry_run, limit=args.limit)

    conn.close()
    print("\nSync complete.")


if __name__ == "__main__":
    try:
        main()
        # Reached here = token OK, both services fetched (0 new events is a
        # normal quiet period, NOT a failure).
        _dh_ok("eusst")
    except SystemExit as _e:
        # sys.exit(1) = missing credentials/config; sys.exit(0)/None = clean.
        if _e.code not in (0, None):
            _dh_fail("eusst", "Config error — missing EU SST credentials in .env")
        raise
    except Exception as _e:
        # RuntimeError from token/fetch failure, or any other error = source down.
        _dh_fail("eusst", f"{type(_e).__name__}: {_e}")
        raise
