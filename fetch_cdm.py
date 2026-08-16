#!/usr/bin/env python3
"""
CAS — Hourly Space-Track CDM Fetcher
=====================================
Cron job: runs as `cas` user, hits /spacetrack/auto on the local engine.
The engine reads ST_IDENTITY / ST_PASSWORD from its own env and writes
results to PostgreSQL.

Crontab entry (crontab -e as cas user, or /etc/cron.d/cas):
    0 * * * *   cas   /opt/cas/fetch_cdm.py >> /var/log/cas/fetch.log 2>&1

No credentials are stored or passed in this script.
"""

import http.client
import json
import sys

# Central data-health tracking
sys.path.insert(0, "/opt/cas/cas_api")
try:
    from core.data_health import report_success as _dh_ok, report_failure as _dh_fail
except Exception as _dh_e:
    print(f"[cdm] data_health import failed ({_dh_e}); health disabled")
    def _dh_ok(*a, **k): pass
    def _dh_fail(*a, **k): pass
import datetime

ENGINE_HOST = "127.0.0.1"
ENGINE_PORT = 8765
ENDPOINT    = "/spacetrack/auto"
TIMEOUT_SEC = 60  # Space-Track round-trip can be slow

# Optional: override fetch window via env (e.g. CAS_FETCH_DAYS=7)
import os
FETCH_DAYS   = int(os.environ.get("CAS_FETCH_DAYS") or "3")
FETCH_MIN_PC = str(os.environ.get("CAS_FETCH_MIN_PC", "0.0001"))


def log(msg: str) -> None:
    ts = datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
    print(f"[{ts}] {msg}", flush=True)


def fetch() -> int:
    """
    POST to /spacetrack/auto. Returns exit code (0 = success).
    """
    payload = json.dumps({
        "days":   FETCH_DAYS,
        "min_pc": FETCH_MIN_PC,
    }).encode("utf-8")

    log(f"Fetching CDMs (days={FETCH_DAYS}, min_pc={FETCH_MIN_PC}) …")

    try:
        conn = http.client.HTTPConnection(ENGINE_HOST, ENGINE_PORT,
                                          timeout=TIMEOUT_SEC)
        conn.request(
            "POST",
            ENDPOINT,
            body=payload,
            headers={
                "Content-Type":   "application/json",
                "Content-Length": str(len(payload)),
            },
        )
        resp = conn.getresponse()
        raw  = resp.read().decode("utf-8")
        conn.close()
    except Exception as exc:
        log(f"ERROR: Could not reach CAS engine — {exc}")
        _dh_fail("cdm", f"Could not reach CAS engine: {exc}")
        return 1

    if resp.status != 200:
        log(f"ERROR: Engine returned HTTP {resp.status}: {raw[:200]}")
        _dh_fail("cdm", f"Engine HTTP {resp.status}: {raw[:200]}")
        return 1

    try:
        data = json.loads(raw)
    except Exception:
        log(f"ERROR: Invalid JSON from engine: {raw[:200]}")
        _dh_fail("cdm", f"Invalid JSON from engine: {raw[:200]}")
        return 1

    total    = data.get("total",       "?")
    red      = data.get("red",         "?")
    yellow   = data.get("yellow",      "?")
    inserted = data.get("db_inserted", "?")

    log(
        f"OK — total={total}  RED={red}  YELLOW={yellow}  "
        f"db_inserted={inserted}"
    )
    # Success = engine reached Space-Track and answered. total=0 is a QUIET day
    # (no new high-Pc conjunctions), NOT a failure — do not alarm on it.
    _dh_ok("cdm")
    return 0


if __name__ == "__main__":
    sys.exit(fetch())
