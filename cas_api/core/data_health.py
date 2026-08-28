"""Centralised data-source health tracking for CAS.
EMPTY upstream data must NEVER overwrite last good data: cron skips INSERT and
calls report_failure(). Mail on FIRST failure and once on recovery. Stale is
source-specific (2x expected interval). Standalone-safe (psycopg2 from DB_URL).

TWO STATUSES, AND THE DIFFERENCE MATTERS.

The data_health.status COLUMN is a latch. report_success() and report_failure()
are the only things that write it, so it records how the last attempt that
actually happened turned out -- and a source that dies silently makes no
attempt, writes nothing, and keeps whatever it last latched. Left alone it says
"ok" forever. Measured 2026-08-28 on casdb_staging, which has no cron by design:
five of thirteen sources read status='ok' while eleven days stale.

That is the exact failure this module exists to catch, hiding in the module's
own output. Anyone running `SELECT source, status FROM data_health` -- which is
the obvious thing to run -- reads "everything is fine" off a dead pipeline.

Staleness cannot be written at report time, because the whole point is that
nothing reports. It is a read-time computation, so get_health() is where it has
to be applied: the status it RETURNS folds staleness in and can say "stale",
while the raw latch stays available as reported_status for anything that needs
it. report_success()'s recovery-mail check reads the column directly and is
deliberately left on the raw value: recovery means "the last attempt failed and
this one did not", which is a statement about attempts.
"""
import os, json, smtplib, datetime
from email.mime.text import MIMEText

_ENV = {}
def _load_env():
    """Environment first, file second.

    This module writes: it updates data_health rows and sends mail. Reading
    the .env of a different instance would mean a staging process reporting
    into the production database. os.environ is authoritative because that is
    what systemd injects per instance; the file is the fallback for cron
    scripts, which inherit no environment.
    """
    if _ENV: return _ENV
    for _k in ("DB_URL", "SMTP_HOST", "SMTP_PORT", "SMTP_USER",
               "SMTP_PASS", "SMTP_FROM", "ALERT_EMAILS"):
        _v = os.environ.get(_k)
        if _v: _ENV[_k] = _v
    if "DB_URL" in _ENV:
        return _ENV
    try:
        from core.paths import CAS_ENV_FILE as _EF
    except Exception:
        # core.paths unavailable: derive the same value instead of falling back
        # to a literal. A staging process reaching production's .env here would
        # report into the production database -- the exact failure the
        # environment-first lookup above exists to prevent.
        _EF = os.path.join(
            os.environ.get("CAS_HOME", "/opt/cas").rstrip("/") or "/opt/cas", ".env")
    try:
        for line in open(_EF):
            line = line.strip()
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                _ENV[k] = v.strip().strip('"').strip("'")
    except FileNotFoundError:
        pass
    return _ENV

def _db():
    import psycopg2
    return psycopg2.connect(_load_env()["DB_URL"])

SOURCES = {
    "space_weather": {"label": "Space Weather (NOAA SWPC)",         "interval": 60},
    "cdm":           {"label": "Conjunction Data (Space-Track)",    "interval": 480},
    "catalog":       {"label": "Object Catalogue (Space-Track)",    "interval": 1440},
    "eusst":         {"label": "EU SST (reentries/fragmentations)", "interval": 360},
    "launch":        {"label": "Launch Schedule (TheSpaceDevs)",    "interval": 240},
    "discos":        {"label": "DISCOS Mass Data (ESA)",            "interval": 10080},
    "satcat":        {"label": "SATCAT Directory (Space-Track)",    "interval": 10080},
    # Not an upstream feed: scripts/backup_db.sh reports here so a backup that
    # silently does not run shows up the same way a dead feed does. The 25-26
    # July 2026 backups were skipped and nothing noticed for two days -- there
    # was no signal to notice. 1440 = daily, so 2x interval makes it stale
    # after 48h, which is the window that went unseen.
    #
    # internal: this one is for us, not for customers. get_all_health() feeds
    # the portal banner that tells operators their data is delayed, and our
    # backup schedule is not their data.
    "backup":        {"label": "Database Backups (pg_dump)",       "interval": 1440,
                      "internal": True},

    # ── Processing steps ────────────────────────────────────────────────────
    #
    # READ THIS BEFORE ADDING AN ENTRY HERE.
    #
    # For every source above, success means "the fetch script reached upstream
    # and returned without raising". That is the right definition there: an
    # upstream that publishes nothing today is not our failure, and fetch_cdm.py
    # says so in as many words ("total=0 is a QUIET day, NOT a failure").
    #
    # For the five entries below that definition is WRONG, and adopting it would
    # reproduce exactly the blindness this section was added to remove. ml_enrich
    # ran to completion on all 38 days it was broken: it read its candidates,
    # called the scorer, got HTTP 401 two hundred times, wrote "DONE scored=0
    # unavailable=0 errors=200", and exited 0. A caller that reported success on
    # "the run finished" would have reported success 114 times while the step
    # did nothing at all.
    #
    # So each entry below states the condition its caller must satisfy, and the
    # CALLER is responsible for evaluating it before choosing report_success()
    # over report_failure(). "Finished" is not "worked".
    #
    # The internal flag is decided per source from where the output surfaces,
    # not from how it feels: get_all_health() drives the portal banner that tells
    # operators their data is delayed, so internal means "a customer cannot see
    # this break", and that is a claim about the UI, checked against the UI.

    "decision_scanner": {"label": "Decision Scanner (watchlist decisions)", "interval": 480},
    #   success: no user's scan raised, AND decisions written == satellites in
    #     watchlist. That equality is an identity, not an estimate -- the scanner
    #     upserts exactly one decision_results row per watchlist row. Measured
    #     2026-08-27: 126 watchlist rows, 126 decision_results rows, all 126
    #     computed within the last 9 hours.
    #   not internal: decision_results feeds the portal's decision panel and the
    #     monthly report (cas_api/services/reporting.py). A stalled scanner shows
    #     customers advice that silently ages, which is precisely what happened
    #     for 38 days.

    "ml_enrich": {"label": "ML Enrichment (Layer-1 scoring)", "interval": 480,
                  "internal": True},
    #   success: errors == 0. NOT "the run finished" -- see the note above; this
    #     is the source that taught us the difference.
    #   internal, on evidence rather than instinct: portal.html
    #     renderMLSection() returns nothing at all when tier is UNAVAILABLE, and
    #     the 70% coverage gate makes every public-CDM event UNAVAILABLE today.
    #     So a customer's screen is byte-identical whether this step works or
    #     not. That is the definition of an ops signal -- and also the reason it
    #     survived 38 days. When operator-tier CDMs start passing the gate this
    #     flag has to be revisited, because then the break becomes visible.

    "relvel_enrich": {"label": "Relative Velocity Enrichment", "interval": 480},
    #   success: the run finished AND miss_tle/candidates stayed under the
    #     script's RELVEL_MISS_MAX_PCT gate. The ratio, not the count: the
    #     candidate pool is whatever has not been filled yet, so a blocked event
    #     stays in it while filled ones leave -- the ratio self-amplifies and a
    #     partial failure climbs into the eighties within days.
    #   not internal: portal.html renders relative velocity as an em-dash when
    #     the field is missing, so the customer sees a blank in the Risk
    #     Assessment panel.

    "rank_debris": {"label": "Top LEO Debris Ranking", "interval": 10080},
    #   success: ranking rows written for the current week > 0. A week that
    #     produces zero rows had no usable input; the table keeps last week's
    #     rows, so nothing visibly breaks and nothing would be noticed.
    #   not internal: served to the catalog page from leo_debris_ranking.

    "directory_satcat": {"label": "Directory Satellite Counts", "interval": 10080},
    #   success: at least one constellation returned a usable count. NOT
    #     "entries updated > 0" -- a week where every count is unchanged is a
    #     normal quiet week, while "every constellation returned no match" is
    #     the signature of Space-Track refusing us, and the two are only
    #     distinguishable by counting matches rather than updates.
    #   not internal: satellite_count is displayed in the public business
    #     directory.
}

def ensure_table():
    """Create the data_health table if it is missing.

    The table is in the Alembic baseline, so this is not how it comes into
    existence any more. It is kept because the only caller is the __main__
    block below -- running this module as a CLI diagnostic against a database
    that has not been migrated yet -- and it is reachable no other way. Nothing
    imports it, so no request path can change schema through here.
    """
    conn = _db(); cur = conn.cursor()
    cur.execute("""CREATE TABLE IF NOT EXISTS data_health (
        source TEXT PRIMARY KEY,
        last_success_at TIMESTAMPTZ,
        last_attempt_at TIMESTAMPTZ,
        status TEXT DEFAULT 'unknown',
        consecutive_failures INT DEFAULT 0,
        last_error TEXT,
        mail_sent_at TIMESTAMPTZ,
        expected_interval_minutes INT)""")
    conn.commit(); cur.close(); conn.close()

def _send_mail(subject, body):
    env = _load_env()
    user = env.get("SMTP_USER", ""); pw = env.get("SMTP_PASS", "")
    if not user or not pw:
        print(f"[data_health] SMTP not configured: {subject}"); return False
    msg = MIMEText(body)
    msg["Subject"] = subject
    msg["From"] = env.get("SMTP_FROM", user)
    msg["To"] = user
    try:
        host = env.get("SMTP_HOST", "mail.privateemail.com")
        server = smtplib.SMTP_SSL(host, 465)
        server.login(user, pw); server.send_message(msg); server.quit()
        print(f"[data_health] mail sent: {subject}"); return True
    except Exception as e:
        print(f"[data_health] mail FAILED: {e}"); return False

def report_success(source):
    meta = SOURCES.get(source, {"label": source, "interval": None})
    conn = _db(); cur = conn.cursor()
    cur.execute("SELECT status, consecutive_failures FROM data_health WHERE source=%s", (source,))
    row = cur.fetchone()
    was_failing = bool(row) and row[0] in ("failed", "degraded") and row[1] > 0
    cur.execute("""INSERT INTO data_health
        (source, last_success_at, last_attempt_at, status, consecutive_failures,
         last_error, expected_interval_minutes)
        VALUES (%s, now(), now(), 'ok', 0, NULL, %s)
        ON CONFLICT (source) DO UPDATE SET
            last_success_at=now(), last_attempt_at=now(), status='ok',
            consecutive_failures=0, last_error=NULL,
            expected_interval_minutes=EXCLUDED.expected_interval_minutes""",
        (source, meta["interval"]))
    conn.commit(); cur.close(); conn.close()
    if was_failing:
        now_utc = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        _send_mail(f"CAS DATA RECOVERED: {meta['label']}",
            f"""The data source below has recovered and is updating normally again.

Source : {meta['label']}
Status : OK - fresh data flowing
Time   : {now_utc}

The {meta['label']} data on CAS Platform is now current. No action needed.
""")

def report_failure(source, error_msg):
    meta = SOURCES.get(source, {"label": source, "interval": None})
    error_msg = (error_msg or "")[:1000]
    conn = _db(); cur = conn.cursor()
    cur.execute("SELECT consecutive_failures FROM data_health WHERE source=%s", (source,))
    row = cur.fetchone()
    prev = row[0] if row else 0
    new_fails = prev + 1
    first = (prev == 0)
    status = "degraded" if new_fails < 3 else "failed"
    cur.execute("""INSERT INTO data_health
        (source, last_attempt_at, status, consecutive_failures, last_error,
         expected_interval_minutes)
        VALUES (%s, now(), %s, %s, %s, %s)
        ON CONFLICT (source) DO UPDATE SET
            last_attempt_at=now(), status=EXCLUDED.status,
            consecutive_failures=EXCLUDED.consecutive_failures,
            last_error=EXCLUDED.last_error,
            expected_interval_minutes=EXCLUDED.expected_interval_minutes""",
        (source, status, new_fails, error_msg, meta["interval"]))
    if first:
        cur.execute("UPDATE data_health SET mail_sent_at=now() WHERE source=%s", (source,))
    conn.commit()
    cur.execute("SELECT last_success_at FROM data_health WHERE source=%s", (source,))
    lg = cur.fetchone()
    last_good = lg[0].strftime("%Y-%m-%d %H:%M UTC") if lg and lg[0] else "unknown"
    cur.close(); conn.close()
    if first:
        now_utc = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        _send_mail(f"CAS DATA SOURCE PROBLEM: {meta['label']}",
            f"""A data source stopped returning usable data. CAS is preserving the last
good data and showing a notice to users; nothing was overwritten.

Source        : {meta['label']}
Status        : {status.upper()}
Detected at   : {now_utc}
Last good data: {last_good}
Error         : {error_msg}

You will get one more mail when the source recovers.
Check: ssh root@213.199.57.173
""")

def get_health(source):
    meta = SOURCES.get(source, {"label": source, "interval": None})
    conn = _db(); cur = conn.cursor()
    cur.execute("""SELECT last_success_at, last_attempt_at, status,
                          consecutive_failures, last_error
                   FROM data_health WHERE source=%s""", (source,))
    row = cur.fetchone(); cur.close(); conn.close()
    if not row:
        return {"source": source, "label": meta["label"], "status": "unknown",
                "reported_status": None,
                "last_success_at": None, "minutes_stale": None, "is_stale": False,
                "internal": meta.get("internal", False)}
    last_success, last_attempt, status, fails, last_error = row
    minutes_stale = None; is_stale = False
    if last_success:
        delta = datetime.datetime.now(datetime.timezone.utc) - last_success
        minutes_stale = int(delta.total_seconds() // 60)
        if meta["interval"]:
            is_stale = minutes_stale > (meta["interval"] * 2)
    else:
        is_stale = True
    # The returned status folds in staleness; the latch is kept beside it.
    # Without this a silently dead source reads "ok" -- see the module docstring.
    effective = status
    if is_stale and status not in ("failed", "degraded"):
        effective = "stale"
    return {"source": source, "label": meta["label"], "status": effective,
            "reported_status": status,
            "last_success_at": last_success.isoformat() if last_success else None,
            "minutes_stale": minutes_stale, "is_stale": is_stale,
            "consecutive_failures": fails,
            # Consumers that show this to customers must skip internal sources;
            # the portal banner does. Kept as data rather than a second
            # accessor so /health/sources stays the one place to look.
            "internal": meta.get("internal", False)}

def get_all_health():
    return {src: get_health(src) for src in SOURCES}

if __name__ == "__main__":
    import sys
    ensure_table()
    if len(sys.argv) > 1:
        print(json.dumps(get_health(sys.argv[1]), indent=2))
    else:
        print(json.dumps(get_all_health(), indent=2))
