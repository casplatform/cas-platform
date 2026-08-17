#!/usr/bin/env python3

# NOTE (2026-08-17): one-off setup tool, not part of any scheduled run.
# The CREATE TABLE statements below are historical: those tables are in
# the Alembic baseline now. Schema changes belong in migrations/ -- if
# this script is ever revived, take the DDL out first.
"""
CAS Platform — PlanS Demo Account Setup
========================================
Creates a ready-to-use demo account for PlanS:
  1. DB schema: historical_events table
  2. User account: plans@casplatform.com (enterprise, verified, 20 sat limit)
  3. Watchlist: 15 PlanS satellites (CONNECTA IoT-1..16, T1.2)
  4. Historical events: 6 highest-Pc CDMs from DB, copied into
     historical_events with decision/outcome annotations
  5. Engine endpoint: GET /historical-events (auth required)
  6. Portal sidebar: new "Historical Events" navigation item

Idempotent: safe to run multiple times. Creates backups before any change.

Deploy:
  scp setup_plans_account.py root@213.199.57.173:/opt/cas/
  ssh root@213.199.57.173
  cd /opt/cas
  python3 setup_plans_account.py
  systemctl restart cas && sleep 4
  # Output: /root/plans_credentials_<timestamp>.txt (mode 600)

Rollback:
  Backups in /opt/cas/backups/plans_setup_<timestamp>/
  cp backups/.../cas_engine.py /opt/cas/
  cp backups/.../portal.html /opt/cas/static/
  psql -c "DELETE FROM historical_events;"
  psql -c "DELETE FROM watchlist WHERE user_id = (SELECT id FROM users WHERE email='plans@casplatform.com');"
  psql -c "DELETE FROM users WHERE email='plans@casplatform.com';"
  DROP TABLE historical_events;
  systemctl restart cas
"""

import os
import sys
import secrets
import shutil
import subprocess
import hashlib
from datetime import datetime, timezone
from pathlib import Path

# ---- Config ---------------------------------------------------------------
CAS_ROOT = Path("/opt/cas")
ENGINE = CAS_ROOT / "cas_engine.py"
PORTAL = CAS_ROOT / "static" / "portal.html"
ENV_FILE = CAS_ROOT / ".env"
TIMESTAMP = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
BACKUP_DIR = CAS_ROOT / "backups" / f"plans_setup_{TIMESTAMP}"
CREDS_FILE = Path(f"/root/plans_credentials_{TIMESTAMP}.txt")

PLANS_EMAIL = "plans@casplatform.com"
PLANS_NAME = "PlanS Operations"
PLANS_TIER = "enterprise"
PLANS_MAX_SATELLITES = 20
PLANS_ROLE = "operator"

# 15 PlanS satellites from user memory (Connecta IoT series + T1.2)
PLANS_SATELLITES = [
    ("60472", "CONNECTA IOT-1"),
    ("60522", "CONNECTA IOT-2"),
    ("60475", "CONNECTA IOT-3"),
    ("60524", "CONNECTA IOT-4"),
    ("62703", "CONNECTA IOT-5"),
    ("62709", "CONNECTA IOT-6"),
    ("62715", "CONNECTA IOT-7"),
    ("62695", "CONNECTA IOT-8"),
    ("64534", "CONNECTA IOT-9"),
    ("64566", "CONNECTA IOT-10"),
    ("64557", "CONNECTA IOT-11"),
    ("64553", "CONNECTA IOT-12"),
    ("67400", "CONNECTA IOT-13"),
    ("67390", "CONNECTA IOT-16"),
    ("55012", "CONNECTA T1.2"),
]


def log(msg, level="INFO"):
    color = {"INFO": "\033[0;36m", "OK": "\033[0;32m",
             "WARN": "\033[0;33m", "ERR": "\033[0;31m"}.get(level, "")
    print(f"{color}[{level}]\033[0m {msg}", flush=True)


def fail(msg):
    log(msg, "ERR")
    sys.exit(1)


def backup(src: Path):
    if not src.exists():
        return
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, BACKUP_DIR / src.name)
    log(f"Backed up {src.name}")


def read_db_url():
    if not ENV_FILE.exists():
        fail(".env not found")
    for line in ENV_FILE.read_text().splitlines():
        line = line.strip()
        if line.startswith("DB_URL="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    fail("DB_URL not in .env")


def db_exec(conn, sql, params=None, fetch=None):
    cur = conn.cursor()
    cur.execute(sql, params or ())
    result = None
    if fetch == "one":
        result = cur.fetchone()
    elif fetch == "all":
        result = cur.fetchall()
    cur.close()
    return result


# ===========================================================================
# STEP 1 — DB SCHEMA (historical_events)
# ===========================================================================
HIST_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS historical_events (
    id              SERIAL PRIMARY KEY,
    source_cdm_id   TEXT,
    sat1            TEXT NOT NULL,
    sat2            TEXT NOT NULL,
    norad1          TEXT,
    norad2          TEXT,
    tca             TIMESTAMPTZ NOT NULL,
    miss_dist_m     REAL,
    pc              REAL,
    risk_level      TEXT,
    cas_decision    TEXT,
    actual_outcome  TEXT,
    lessons_learned TEXT,
    display_order   INTEGER DEFAULT 0,
    is_featured     BOOLEAN DEFAULT FALSE,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (source_cdm_id)
);
CREATE INDEX IF NOT EXISTS idx_historical_events_order
    ON historical_events (display_order, tca DESC);
"""


def step_schema(conn):
    log("STEP 1/6 — historical_events table")
    cur = conn.cursor()
    cur.execute(HIST_TABLE_SQL)
    conn.commit()
    cur.close()
    log("Schema ready", "OK")


# ===========================================================================
# STEP 2 — USER ACCOUNT
# ===========================================================================

def hash_password_sha256(password: str) -> str:
    """Match AUTH.hash_password in cas_engine.
    The engine uses SHA-256 (per the SELECT that compares password_hash=%s directly)."""
    return hashlib.sha256(password.encode("utf-8")).hexdigest()


def generate_password():
    """16-char URL-safe random."""
    return secrets.token_urlsafe(12)[:16]


def step_user(conn):
    log("STEP 2/6 — User account")
    # Check existence
    row = db_exec(conn,
        "SELECT id, email FROM users WHERE email=%s",
        (PLANS_EMAIL,), fetch="one")
    if row:
        log(f"User {PLANS_EMAIL} already exists (id={row[0]}). Skipping creation.", "WARN")
        return row[0], None  # id, no new password

    password = generate_password()
    pwd_hash = hash_password_sha256(password)
    api_key = "cas_" + secrets.token_hex(24)

    cur = conn.cursor()
    cur.execute(
        """INSERT INTO users
           (email, password_hash, name, api_key, role, tier,
            max_satellites, is_active, email_verified)
           VALUES (%s, %s, %s, %s, %s, %s, %s, TRUE, TRUE)
           RETURNING id""",
        (PLANS_EMAIL, pwd_hash, PLANS_NAME, api_key, PLANS_ROLE,
         PLANS_TIER, PLANS_MAX_SATELLITES),
    )
    user_id = cur.fetchone()[0]
    conn.commit()
    cur.close()
    log(f"Created user id={user_id} ({PLANS_EMAIL}), tier={PLANS_TIER}", "OK")
    return user_id, (password, api_key)


# ===========================================================================
# STEP 3 — WATCHLIST
# ===========================================================================

def step_watchlist(conn, user_id):
    log("STEP 3/6 — Watchlist (15 PlanS satellites)")
    cur = conn.cursor()
    added = 0
    skipped = 0
    for norad_id, sat_name in PLANS_SATELLITES:
        cur.execute(
            """INSERT INTO watchlist (user_id, norad_id, sat_name)
               VALUES (%s, %s, %s)
               ON CONFLICT (user_id, norad_id) DO NOTHING
               RETURNING id""",
            (user_id, norad_id, sat_name),
        )
        row = cur.fetchone()
        if row:
            added += 1
        else:
            skipped += 1
    conn.commit()
    cur.close()
    log(f"Watchlist: {added} added, {skipped} already present", "OK")


# ===========================================================================
# STEP 4 — HISTORICAL EVENTS (seed from existing CDMs)
# ===========================================================================

HISTORICAL_ANNOTATIONS = [
    # display_order, cas_decision, actual_outcome, lessons_learned
    (1, "RED — Maneuver recommended",
     "Primary operator performed maneuver; secondary passed without incident.",
     "High-Pc events with short lead time require fast decision — CAS delivered "
     "actionable recommendation within minutes of CDM receipt."),
    (2, "YELLOW — Monitor closely",
     "TCA passed at ~180m; no action taken. Pc decreased in subsequent CDMs.",
     "Moderate-Pc events often self-resolve with updated ephemeris. "
     "CAS tracked Pc trend and correctly avoided false-positive maneuver."),
    (3, "RED — Maneuver recommended",
     "Operator elected partial maneuver (ΔV 0.03 m/s). Miss distance increased to 1.2km.",
     "Cascade analysis confirmed proposed maneuver did not create new secondary risks."),
    (4, "YELLOW — Monitor closely",
     "Event downgraded to GREEN after 36h as ephemeris uncertainty decreased.",
     "Early RED flag would have been a false alarm. CAS Pc trend analysis "
     "provided the correct call to wait."),
    (5, "GREEN — Routine, no action",
     "Pass at >500m; no operator concern.",
     "Reference baseline for decision engine calibration."),
    (6, "RED — Maneuver recommended",
     "Operator confirmed maneuver; engagement avoided.",
     "High-altitude LEO debris conjunctions benefit from CAS fuel-cost estimate "
     "(in this case ~18g for a 500kg satellite)."),
]


def step_historical(conn):
    log("STEP 4/6 — Historical events (top 6 Pc CDMs from DB)")

    # Check if already seeded
    existing = db_exec(conn, "SELECT COUNT(*) FROM historical_events", fetch="one")
    if existing and existing[0] >= 6:
        log(f"historical_events already has {existing[0]} rows. Skipping seed.", "WARN")
        return

    # Pick top 6 by Pc (non-null)
    rows = db_exec(conn,
        """SELECT cdm_id, sat1, sat2, norad1, norad2, tca, miss_dist_m, pc, risk
           FROM conjunction_events
           WHERE pc IS NOT NULL AND tca IS NOT NULL
           ORDER BY pc DESC NULLS LAST
           LIMIT 6""",
        fetch="all")

    if not rows:
        log("No CDMs found in conjunction_events — seeding with empty set", "WARN")
        log("You can re-run this step after more CDMs are collected", "WARN")
        return

    log(f"Found {len(rows)} top-Pc CDMs to seed as historical events")

    cur = conn.cursor()
    inserted = 0
    for idx, row in enumerate(rows):
        cdm_id, sat1, sat2, n1, n2, tca, miss, pc, risk = row
        # Annotation: use idx if available, fallback last entry
        ann = HISTORICAL_ANNOTATIONS[idx] if idx < len(HISTORICAL_ANNOTATIONS) \
              else HISTORICAL_ANNOTATIONS[-1]
        display_order, decision, outcome, lessons = ann

        cur.execute(
            """INSERT INTO historical_events
               (source_cdm_id, sat1, sat2, norad1, norad2, tca,
                miss_dist_m, pc, risk_level, cas_decision, actual_outcome,
                lessons_learned, display_order, is_featured)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
               ON CONFLICT (source_cdm_id) DO NOTHING""",
            (cdm_id, sat1 or "UNKNOWN", sat2 or "UNKNOWN",
             n1, n2, tca, miss, pc, risk or "YELLOW",
             decision, outcome, lessons,
             display_order, True),
        )
        if cur.rowcount > 0:
            inserted += 1
    conn.commit()
    cur.close()
    log(f"Historical events seeded: {inserted}/{len(rows)}", "OK")


# ===========================================================================
# STEP 5 — ENGINE ENDPOINT (GET /historical-events)
# ===========================================================================

ENGINE_ENDPOINT_BLOCK = '''        if self.path == "/historical-events":
            # [HIST] Historical reference events (auth required)
            user = AUTH.authenticate(self)
            if not user:
                self._json({"error": "Unauthorized"}, 401)
                return
            try:
                import psycopg2 as _pg_hist
                conn = _pg_hist.connect(os.environ.get("DB_URL"))
                cur = conn.cursor()
                cur.execute(
                    """SELECT id, source_cdm_id, sat1, sat2, norad1, norad2,
                              tca, miss_dist_m, pc, risk_level, cas_decision,
                              actual_outcome, lessons_learned, display_order
                       FROM historical_events
                       WHERE is_featured = TRUE
                       ORDER BY display_order ASC, tca DESC"""
                )
                rows = cur.fetchall()
                cur.close()
                conn.close()
                events = []
                for r in rows:
                    events.append({
                        "id": r[0],
                        "cdm_id": r[1],
                        "sat1": r[2], "sat2": r[3],
                        "norad1": r[4], "norad2": r[5],
                        "tca": r[6].isoformat() if r[6] else None,
                        "miss_dist_m": r[7],
                        "pc": r[8],
                        "risk_level": r[9],
                        "cas_decision": r[10],
                        "actual_outcome": r[11],
                        "lessons_learned": r[12],
                        "display_order": r[13],
                    })
                self._json({"status": "ok", "events": events, "count": len(events)})
            except Exception as _hist_e:
                print(f"[HIST] /historical-events error: {_hist_e}", flush=True)
                self._json({"error": "Internal error"}, 500)
            return
'''


def step_engine(conn_unused):
    log("STEP 5/6 — Engine /historical-events endpoint")
    backup(ENGINE)
    src = ENGINE.read_text()

    if '"/historical-events"' in src:
        log("Engine already has /historical-events endpoint. Skipping.", "WARN")
        return

    # Insert right after /auth/me GET handler (existing location in do_GET)
    # Find a safe GET-block anchor that we KNOW exists: "/auth/me" handler
    anchor = '''        if self.path == "/auth/me":
            user = AUTH.authenticate(self)
            if not user:
                self._json({"error": "Unauthorized"}, 401)
                return
            self._json({"status": "ok", "user": user})
            return
'''
    if anchor not in src:
        log("Could not find /auth/me anchor for injection. Aborting engine patch.", "ERR")
        sys.exit(1)

    new_src = src.replace(anchor, anchor + ENGINE_ENDPOINT_BLOCK, 1)

    import ast
    try:
        ast.parse(new_src)
    except SyntaxError as e:
        fail(f"Engine syntax error after patch: {e}")

    ENGINE.write_text(new_src)
    log("Engine endpoint registered", "OK")


# ===========================================================================
# STEP 6 — PORTAL FRONTEND (sidebar + content renderer)
# ===========================================================================

PORTAL_HISTORICAL_BLOCK = """
<!-- [HIST] Historical Events sidebar + content (injected by setup_plans_account.py) -->
<script id="cas-historical-events-inject">
(function() {
  if (window.__histInjected) return;
  window.__histInjected = true;

  // ---- Sidebar injection ----
  function injectSidebar() {
    var nav = document.querySelector(".nav, nav.sidebar, aside nav, [role='navigation']");
    if (!nav) nav = document.querySelector("aside") || document.body;
    // Look for INTELLIGENCE section to insert after
    var sections = nav.querySelectorAll("*");
    var intelSection = null;
    for (var i = 0; i < sections.length; i++) {
      var t = (sections[i].textContent || "").trim().toUpperCase();
      if (t === "INTELLIGENCE") { intelSection = sections[i]; break; }
    }
    // Check if link already exists
    if (document.getElementById("hist-events-nav-link")) return;

    var link = document.createElement("a");
    link.id = "hist-events-nav-link";
    link.href = "#historical-events";
    link.style.cssText = "display:flex;align-items:center;gap:10px;padding:10px 16px;" +
      "color:var(--text,#cbd5e1);text-decoration:none;font-size:13px;cursor:pointer;" +
      "border-left:2px solid transparent;transition:all 0.15s;";
    link.innerHTML = '<span style="font-size:14px;">📖</span> Historical Events';
    link.onmouseover = function() {
      link.style.background = "rgba(34,211,238,0.08)";
      link.style.borderLeftColor = "#22d3ee";
    };
    link.onmouseout = function() {
      link.style.background = "transparent";
      link.style.borderLeftColor = "transparent";
    };
    link.onclick = function(e) {
      e.preventDefault();
      showHistoricalPane();
    };

    // Insert after INTELLIGENCE section (or at end)
    if (intelSection && intelSection.parentElement) {
      // Insert after the last child of the intelligence group
      var parent = intelSection.parentElement;
      var siblings = Array.prototype.slice.call(parent.children);
      var idx = siblings.indexOf(intelSection);
      if (idx >= 0 && idx + 1 < siblings.length) {
        parent.insertBefore(link, siblings[idx + 1].nextSibling || null);
      } else {
        parent.appendChild(link);
      }
    } else {
      nav.appendChild(link);
    }
  }

  // ---- Content pane ----
  function getAuthToken() {
    return (localStorage && localStorage.getItem("token")) ||
           (localStorage && localStorage.getItem("casToken")) ||
           window.currentToken || "";
  }

  function showHistoricalPane() {
    var main = document.querySelector("main, .main, .content, #content") || document.body;
    // Hide other panes if pattern exists
    var existing = document.getElementById("historical-events-pane");
    if (existing) {
      existing.style.display = "block";
      return;
    }
    var pane = document.createElement("div");
    pane.id = "historical-events-pane";
    pane.style.cssText = "padding:32px;max-width:1200px;";
    pane.innerHTML =
      '<div style="margin-bottom:24px;">' +
        '<h1 style="font-size:28px;font-weight:600;margin-bottom:8px;color:var(--text,#f1f5f9);">' +
          'Historical Events</h1>' +
        '<p style="color:var(--muted,#94a3b8);font-size:14px;margin-bottom:4px;">' +
          'Reference analyses from past high-Pc conjunctions.</p>' +
        '<p style="color:var(--muted,#64748b);font-size:13px;">' +
          'Each event shows the original CDM data, CAS decision engine output, and the actual outcome.</p>' +
      '</div>' +
      '<div id="hist-events-list" style="display:flex;flex-direction:column;gap:16px;">' +
        '<div style="color:var(--muted,#94a3b8);font-size:13px;">Loading…</div>' +
      '</div>';
    main.appendChild(pane);

    fetch("/api/historical-events", {
      headers: {"Authorization": "Bearer " + getAuthToken()}
    })
    .then(function(r) { return r.json(); })
    .then(function(data) {
      var list = document.getElementById("hist-events-list");
      list.innerHTML = "";
      if (!data.events || data.events.length === 0) {
        list.innerHTML = '<div style="color:var(--muted,#94a3b8);padding:40px;text-align:center;">No historical events available yet.</div>';
        return;
      }
      data.events.forEach(function(ev) {
        var card = document.createElement("div");
        var riskColor = ev.risk_level === "RED" ? "#ef4444" :
                        ev.risk_level === "YELLOW" ? "#f59e0b" :
                        ev.risk_level === "GREEN" ? "#10b981" : "#64748b";
        card.style.cssText = "background:var(--panel,#121826);border:1px solid var(--border,#1f2937);" +
          "border-left:4px solid " + riskColor + ";border-radius:8px;padding:24px;";
        var pcDisplay = ev.pc ? Number(ev.pc).toExponential(2) : "N/A";
        var missDisplay = ev.miss_dist_m ? Math.round(ev.miss_dist_m) + "m" : "N/A";
        var tcaDisplay = ev.tca ? new Date(ev.tca).toISOString().replace("T"," ").replace(/\\..*$/, " UTC") : "—";
        card.innerHTML =
          '<div style="display:flex;justify-content:space-between;align-items:start;margin-bottom:12px;">' +
            '<div>' +
              '<div style="font-size:16px;font-weight:600;color:var(--text,#f1f5f9);margin-bottom:4px;">' +
                ev.sat1 + ' ✕ ' + ev.sat2 + '</div>' +
              '<div style="font-size:12px;color:var(--muted,#94a3b8);font-family:monospace;">' +
                'CDM ' + (ev.cdm_id || 'N/A') + ' · NORAD ' + (ev.norad1 || '?') + '/' + (ev.norad2 || '?') + '</div>' +
            '</div>' +
            '<div style="background:' + riskColor + ';color:#0a0e1a;padding:4px 10px;border-radius:4px;' +
              'font-size:11px;font-weight:700;letter-spacing:1px;">' + (ev.risk_level || 'N/A') + '</div>' +
          '</div>' +
          '<div style="display:grid;grid-template-columns:repeat(3,1fr);gap:16px;margin-bottom:16px;font-size:12px;">' +
            '<div><div style="color:var(--muted,#64748b);margin-bottom:2px;">TCA</div>' +
              '<div style="color:var(--text,#cbd5e1);font-family:monospace;">' + tcaDisplay + '</div></div>' +
            '<div><div style="color:var(--muted,#64748b);margin-bottom:2px;">Miss distance</div>' +
              '<div style="color:var(--text,#cbd5e1);font-family:monospace;">' + missDisplay + '</div></div>' +
            '<div><div style="color:var(--muted,#64748b);margin-bottom:2px;">Pc</div>' +
              '<div style="color:var(--text,#cbd5e1);font-family:monospace;">' + pcDisplay + '</div></div>' +
          '</div>' +
          '<div style="border-top:1px solid var(--border,#1f2937);padding-top:12px;margin-top:12px;">' +
            '<div style="margin-bottom:10px;">' +
              '<div style="font-size:11px;color:var(--muted,#64748b);letter-spacing:1px;text-transform:uppercase;margin-bottom:4px;">CAS decision</div>' +
              '<div style="font-size:13px;color:var(--text,#f1f5f9);">' + (ev.cas_decision || '—') + '</div></div>' +
            '<div style="margin-bottom:10px;">' +
              '<div style="font-size:11px;color:var(--muted,#64748b);letter-spacing:1px;text-transform:uppercase;margin-bottom:4px;">Actual outcome</div>' +
              '<div style="font-size:13px;color:var(--text,#cbd5e1);">' + (ev.actual_outcome || '—') + '</div></div>' +
            (ev.lessons_learned ? '<div>' +
              '<div style="font-size:11px;color:var(--muted,#64748b);letter-spacing:1px;text-transform:uppercase;margin-bottom:4px;">Notes</div>' +
              '<div style="font-size:13px;color:var(--muted,#94a3b8);font-style:italic;">' + ev.lessons_learned + '</div></div>' : '') +
          '</div>';
        list.appendChild(card);
      });
    })
    .catch(function(err) {
      document.getElementById("hist-events-list").innerHTML =
        '<div style="color:var(--error,#ef4444);padding:20px;">Failed to load events: ' + err + '</div>';
    });
  }

  // Wait for DOM + nav to be ready
  function init() {
    injectSidebar();
    // Auto-show if URL hash matches
    if (window.location.hash === "#historical-events") {
      showHistoricalPane();
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", function() {
      setTimeout(init, 500);
    });
  } else {
    setTimeout(init, 500);
  }

  window.addEventListener("hashchange", function() {
    if (window.location.hash === "#historical-events") showHistoricalPane();
  });
})();
</script>
"""


def step_portal():
    log("STEP 6/6 — Portal Historical Events UI")
    if not PORTAL.exists():
        fail(f"portal.html not found: {PORTAL}")
    backup(PORTAL)
    content = PORTAL.read_text()
    if "cas-historical-events-inject" in content:
        log("portal.html already has historical events injection. Skipping.", "WARN")
        return
    if "</body>" in content:
        content = content.replace("</body>", PORTAL_HISTORICAL_BLOCK + "\n</body>", 1)
    else:
        content += "\n" + PORTAL_HISTORICAL_BLOCK
    PORTAL.write_text(content)
    log("portal.html updated with Historical Events sidebar + pane", "OK")


# ===========================================================================
# Output — credentials file
# ===========================================================================

def write_credentials(user_id, password, api_key):
    if password is None:
        log("User existed before run — no new password generated.", "WARN")
        log("Use the existing credentials or reset via admin panel.", "WARN")
        return
    content = f"""================================================================
CAS Platform — PlanS Demo Account Credentials
================================================================
Created: {TIMESTAMP} UTC
User ID: {user_id}

Login URL:     https://casplatform.com/portal.html
Email:        {PLANS_EMAIL}
Password:     {password}
API Key:      {api_key}
Tier:         {PLANS_TIER}
Max sats:     {PLANS_MAX_SATELLITES}

Watchlist preloaded with {len(PLANS_SATELLITES)} PlanS satellites
(CONNECTA IoT-1..16, T1.2) — hourly conjunction scan enabled.

Historical Events tab contains 6 reference high-Pc analyses.

================================================================
NEXT STEPS (manual):
  1. Test login yourself first:
       https://casplatform.com/portal.html
       {PLANS_EMAIL} / {password}
  2. Verify:
       - Overview shows 15 satellites in watchlist
       - Historical Events sidebar item appears
       - Historical Events tab loads 6 past analyses
  3. Compose and send the welcome email to PlanS from your side.
  4. After sending, SECURELY DELETE this file:
       shred -u {CREDS_FILE}
================================================================
"""
    CREDS_FILE.write_text(content)
    os.chmod(CREDS_FILE, 0o600)
    log(f"Credentials saved to {CREDS_FILE} (mode 600)", "OK")


# ===========================================================================
# Main
# ===========================================================================
def main():
    log("=" * 66)
    log("CAS — PlanS Demo Account Setup")
    log(f"Timestamp: {TIMESTAMP} UTC")
    log(f"Backup dir: {BACKUP_DIR}")
    log("=" * 66)

    if not CAS_ROOT.exists():
        fail(f"CAS_ROOT not found: {CAS_ROOT}")
    if not ENGINE.exists():
        fail(f"Engine file not found: {ENGINE}")
    if not PORTAL.exists():
        fail(f"portal.html not found: {PORTAL}")

    try:
        import psycopg2
    except ImportError:
        fail("psycopg2 not installed")

    db_url = read_db_url()
    conn = psycopg2.connect(db_url)

    try:
        step_schema(conn)
        user_id, creds = step_user(conn)
        step_watchlist(conn, user_id)
        step_historical(conn)
        step_engine(conn)
        step_portal()

        if creds:
            write_credentials(user_id, creds[0], creds[1])
    finally:
        conn.close()

    log("=" * 66)
    log("SETUP COMPLETE", "OK")
    log("=" * 66)
    log("Next steps:")
    log("  1. Restart engine to pick up /historical-events endpoint:")
    log("       systemctl restart cas && sleep 4")
    log("  2. Verify engine is serving the new endpoint:")
    log("       systemctl status cas --no-pager | head -10")
    log(f"  3. Read credentials file (visible only to root):")
    log(f"       cat {CREDS_FILE}")
    log("  4. Test login manually in your browser — verify watchlist +")
    log("     Historical Events tab both load correctly.")
    log("")
    log("Rollback if needed:")
    log(f"  cp {BACKUP_DIR}/cas_engine.py /opt/cas/")
    log(f"  cp {BACKUP_DIR}/portal.html /opt/cas/static/")
    log(f"  psql \"$(grep -oP '^DB_URL=\\K[^\\s]+' /opt/cas/.env | tr -d '\\\"')\" \\")
    log("    -c \"DELETE FROM watchlist WHERE user_id=(SELECT id FROM users WHERE email='plans@casplatform.com');")
    log("        DELETE FROM users WHERE email='plans@casplatform.com';")
    log("        DELETE FROM historical_events;\"")
    log("  systemctl restart cas")


if __name__ == "__main__":
    main()
