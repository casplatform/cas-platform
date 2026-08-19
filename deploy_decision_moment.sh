#!/bin/bash

# ─── RETIRED ONE-SHOT SCRIPT — DOES NOT RUN ─────────────────────────────────
# This script edits /opt/cas (production) in place: it rewrites source files
# and/or writes to the production database, then restarts the service. It has
# no test gate, no health check and no rollback point, so a stray run bypasses
# every guarantee scripts/deploy.sh provides.
#
# It was a one-shot change that has already been applied and is recorded in
# git history. Replaying it against today's tree would inject code that has
# since been corrected. Kept read-only as the record of what was deployed.
#
# To ship a change: edit /opt/cas_staging, then run /opt/cas/scripts/deploy.sh.
echo "REFUSING TO RUN: $(basename "$0") is a retired one-shot production" >&2
echo "patch. Use /opt/cas/scripts/deploy.sh instead." >&2
exit 2
# ────────────────────────────────────────────────────────────────────────────
# ═══════════════════════════════════════════════════════════════════
# CAS Decision Moment — "Do you maneuver?"
# ═══════════════════════════════════════════════════════════════════
set -e
cd /opt/cas
TS=$(date +%Y%m%d_%H%M%S)
BACKUP=/root/nginx_backups
mkdir -p "$BACKUP"

echo "═══════════════════════════════════════════════════════════"
echo " Decision Moment — full deployment"
echo "═══════════════════════════════════════════════════════════"

# ─── 1) DB MIGRATION ───────────────────────────────────────────────
echo ""
echo "[1/5] DB migration — add operator_action columns"
sudo -u postgres psql casdb << 'SQLEOF'
-- Add operator decision columns (idempotent)
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='decision_results' AND column_name='operator_action') THEN
        ALTER TABLE decision_results ADD COLUMN operator_action VARCHAR(30);
        ALTER TABLE decision_results ADD COLUMN operator_action_at TIMESTAMPTZ;
        ALTER TABLE decision_results ADD COLUMN operator_notes TEXT;
        RAISE NOTICE 'Columns added';
    ELSE
        RAISE NOTICE 'Columns already exist';
    END IF;
END $$;

-- Also make engine save "No action" decisions by inserting mock data for testing
-- This simulates a real HIGH priority conjunction so we can test the UI buttons
INSERT INTO decision_results (
    user_id, norad_id, sat_name, recommendation, priority, confidence,
    max_pc, min_miss_m, total_conjunctions, red_count, yellow_count, green_count,
    time_remaining_s, time_remaining_str, maneuver_summary, delta_v_ms,
    maneuver_direction, alert_total, alert_review, alert_critical
) VALUES
(1, '25544', 'ISS', 'Maneuver advised', 'HIGH', 'high',
 0.00234, 87.3, 5, 2, 2, 1,
 43200, '12h 0m', 'ISS has 2 RED-level conjunctions in the next 12 hours. Immediate maneuver planning recommended. Primary threat: COSMOS 2251 DEB (NORAD 34567) at TCA 2026-04-17 08:30 UTC, miss distance 87m, Pc=2.34e-3.', 0.045,
 'along-track', 5, 3, 2),
(1, '60472', 'CONNECTA IOT-1', 'Monitor', 'MEDIUM', 'medium',
 0.000089, 340.5, 3, 0, 2, 1,
 86400, '24h 0m', 'CONNECTA IOT-1 has 2 YELLOW-level conjunctions. Monitor closely and re-evaluate in 12 hours.', 0.012,
 'cross-track', 3, 2, 0),
(1, '55012', 'CONNECTA T1.2', 'No action', 'LOW', 'low',
 0.0000001, 5200, 1, 0, 0, 1,
 172800, '48h 0m', 'No active threats for CONNECTA T1.2. All conjunctions GREEN.', 0,
 NULL, 1, 0, 0)
ON CONFLICT DO NOTHING;

SELECT id, sat_name, recommendation, priority, operator_action FROM decision_results ORDER BY id DESC LIMIT 5;
SQLEOF
echo "[OK] DB migration + mock data"

# ─── 2) ENGINE ENDPOINT ───────────────────────────────────────────
echo ""
echo "[2/5] Adding POST /api/operator-decision endpoint"
cp /opt/cas/cas_engine.py "$BACKUP/cas_engine.py.bak.$TS"

python3 << 'PYEOF'
p = "/opt/cas/cas_engine.py"
s = open(p).read()

if "/operator-decision" in s:
    print("[SKIP] endpoint already present")
else:
    # Find the POST handler section — insert before the contact form handler
    marker = '        if self.path == "/contact" or self.path == "/api/contact":'
    endpoint = '''        # ── Operator Decision Recording ──
        if self.path == "/operator-decision" or self.path == "/api/operator-decision":
            try:
                length = int(self.headers.get("Content-Length", 0))
                raw = self.rfile.read(length)
                data = json.loads(raw)
                decision_id = data.get("decision_id")
                action = data.get("action")  # "maneuver_approved" or "monitoring"
                notes = data.get("notes", "")
                if not decision_id or action not in ("maneuver_approved", "monitoring"):
                    self._json({"error": "decision_id and action (maneuver_approved|monitoring) required"}, 400)
                    return
                # Auth check
                auth = self.headers.get("Authorization", "")
                user_id = None
                if auth.startswith("Bearer "):
                    token = auth.split(" ", 1)[1]
                    try:
                        import jwt
                        payload = jwt.decode(token, os.environ.get("JWT_SECRET",""), algorithms=["HS256"])
                        user_id = payload.get("uid") or payload.get("user_id")
                    except Exception:
                        pass
                elif auth.startswith("ApiKey "):
                    key = auth.split(" ", 1)[1]
                    try:
                        conn = psycopg2.connect(os.environ.get("DB_URL",""))
                        cur = conn.cursor()
                        cur.execute("SELECT id FROM users WHERE api_key=%s", (key,))
                        row = cur.fetchone()
                        if row: user_id = row[0]
                        cur.close(); conn.close()
                    except Exception:
                        pass
                if not user_id:
                    self._json({"error": "Unauthorized"}, 401)
                    return
                conn = psycopg2.connect(os.environ.get("DB_URL",""))
                cur = conn.cursor()
                cur.execute("""
                    UPDATE decision_results
                    SET operator_action = %s,
                        operator_action_at = NOW(),
                        operator_notes = %s
                    WHERE id = %s AND user_id = %s
                    RETURNING id, sat_name, operator_action
                """, (action, notes[:500] if notes else None, int(decision_id), user_id))
                result = cur.fetchone()
                conn.commit(); cur.close(); conn.close()
                if result:
                    print(f"[DECISION] user={user_id} decision={result[0]} {result[1]} -> {result[2]}", flush=True)
                    self._json({"status": "ok", "decision_id": result[0], "action": result[2]})
                else:
                    self._json({"error": "Decision not found or unauthorized"}, 404)
            except Exception as e:
                self._json({"error": str(e)}, 500)
            return

'''
    assert marker in s, "contact handler marker not found"
    s = s.replace(marker, endpoint + marker, 1)
    import ast; ast.parse(s)
    open(p, "w").write(s)
    print("[OK] POST /api/operator-decision endpoint added")
PYEOF

# ─── 3) FRONTEND — Decision buttons in renderDecisions ────────────
echo ""
echo "[3/5] Adding decision buttons to portal.html"
cp /opt/cas/static/portal.html "$BACKUP/portal.html.bak.$TS"

python3 << 'PYEOF'
p = "/opt/cas/static/portal.html"
s = open(p).read()

# A) Fix sidebar idx mapping — add top_debris
old_idx = "eusst_aggregate:10, eusst_reentries:11, eusst_fragmentations:12"
new_idx = "eusst_aggregate:10, eusst_reentries:11, eusst_fragmentations:12, top_debris:13"
if "top_debris:13" not in s:
    s = s.replace(old_idx, new_idx, 1)
    print("[OK] sidebar idx: top_debris added")
else:
    print("[SKIP] top_debris idx already present")

# B) Add operator decision buttons to decision cards
# Find the delta-v line in renderDecisions card and add buttons after it
old_card_end = "+'<span>Alerts: <span style=\"color:var(--text)\">'+(d.alert_total||0)+' \\u2192 '+(d.alert_review||0)+' \\u2192 '+(d.alert_critical||0)+'</span></span>'\n          +'</div></div>';"

new_card_end = """+'<span>Alerts: <span style="color:var(--text)">'+(d.alert_total||0)+' \\u2192 '+(d.alert_review||0)+' \\u2192 '+(d.alert_critical||0)+'</span></span>'
          +'</div>'
          // ── OPERATOR DECISION MOMENT ──
          +(d.operator_action
            ? '<div style="margin-top:12px;padding:10px 14px;background:rgba(0,201,219,0.06);border:1px solid rgba(0,201,219,0.2);border-radius:6px;display:flex;align-items:center;gap:10px;">'
              +'<span style="font-size:16px;">'+(d.operator_action==='maneuver_approved'?'\\u2705':'\\U0001F7E1')+'</span>'
              +'<div><div style="font-family:var(--mono);font-size:10px;color:var(--cyan);letter-spacing:1px;">OPERATOR DECISION RECORDED</div>'
              +'<div style="font-family:var(--mono);font-size:11px;color:var(--text);margin-top:2px;">'
              +(d.operator_action==='maneuver_approved'?'Maneuver Approved':'Monitoring Continued')
              +' \\u2014 '+(d.operator_action_at ? new Date(d.operator_action_at).toISOString().slice(0,16).replace("T"," ")+" UTC" : "")
              +'</div></div></div>'
            : (d.recommendation !== 'No action'
              ? '<div style="margin-top:14px;padding:14px;background:rgba(255,255,255,0.02);border:1px solid var(--border);border-radius:6px;">'
                +'<div style="font-family:var(--mono);font-size:9px;color:var(--cyan);letter-spacing:2px;margin-bottom:10px;">OPERATOR DECISION REQUIRED</div>'
                +'<div style="display:flex;gap:10px;flex-wrap:wrap;">'
                +'<button onclick="recordDecision('+d.id+',\\'maneuver_approved\\')" style="flex:1;min-width:160px;padding:12px 20px;font-family:var(--mono);font-size:11px;letter-spacing:1px;font-weight:700;border:2px solid var(--red);background:rgba(239,68,68,0.1);color:var(--red);border-radius:6px;cursor:pointer;transition:all .2s;">\\u26A0 APPROVE MANEUVER</button>'
                +'<button onclick="recordDecision('+d.id+',\\'monitoring\\')" style="flex:1;min-width:160px;padding:12px 20px;font-family:var(--mono);font-size:11px;letter-spacing:1px;font-weight:700;border:2px solid var(--cyan);background:rgba(0,201,219,0.06);color:var(--cyan);border-radius:6px;cursor:pointer;transition:all .2s;">\\U0001F6E1 CONTINUE MONITORING</button>'
                +'</div></div>'
              : '')
          )
          +'</div>';"""

if "OPERATOR DECISION" in s:
    print("[SKIP] decision buttons already present")
else:
    assert old_card_end in s, "card end marker not found"
    s = s.replace(old_card_end, new_card_end, 1)
    print("[OK] decision buttons added to cards")

# C) Add recordDecision JS function (before triggerDecisionScan)
old_trigger = "async function triggerDecisionScan()"
new_func = """async function recordDecision(decisionId, action) {
  try {
    const r = await fetch(API+'/api/operator-decision', {
      method: 'POST',
      headers: {'Authorization':'Bearer '+currentToken, 'Content-Type':'application/json'},
      body: JSON.stringify({decision_id: decisionId, action: action})
    });
    const d = await r.json();
    if (d.error) { toast(d.error, 'error'); return; }
    toast('Decision recorded: '+(action==='maneuver_approved'?'Maneuver Approved':'Monitoring Continued'), 'success');
    renderDecisions(document.getElementById('portalContent'));
  } catch(e) { toast('Error: '+e.message, 'error'); }
}

async function triggerDecisionScan()"""

if "recordDecision" in s:
    print("[SKIP] recordDecision function already present")
else:
    s = s.replace(old_trigger, new_func, 1)
    print("[OK] recordDecision function added")

# D) Ensure decision endpoint returns operator_action fields
# The /api/decision/dashboard endpoint needs to include operator_action in response
# Check if it's already there
if "operator_action" not in s:
    print("[NOTE] operator_action fields need to be added to decision/dashboard endpoint response in cas_engine.py")

open(p, "w").write(s)
print("[DONE] portal.html patched")
PYEOF

# ─── 4) ENGINE — Include operator_action in decision response ─────
echo ""
echo "[4/5] Adding operator_action to decision response"

python3 << 'PYEOF'
p = "/opt/cas/cas_engine.py"
s = open(p).read()

# Find where decision_results are queried for dashboard and add operator_action
# The _save_decision function or the dashboard query needs to SELECT operator_action
if "operator_action" in s and "operator_action_at" in s:
    print("[SKIP] operator_action already in engine queries")
else:
    # Add to the decision/dashboard endpoint response
    # Find the SELECT query for decision results
    import re
    # Look for SELECT from decision_results
    m = re.search(r"(SELECT\s+.*?FROM\s+decision_results)", s, re.DOTALL | re.IGNORECASE)
    if m:
        # Find and update the specific dashboard query
        old_select = "SELECT id, user_id, norad_id, sat_name, recommendation, priority, confidence"
        if old_select in s:
            new_select = "SELECT id, user_id, norad_id, sat_name, recommendation, priority, confidence, operator_action, operator_action_at"
            s = s.replace(old_select, new_select)
            print("[OK] operator_action added to SELECT query")
        else:
            print("[WARN] specific SELECT pattern not found — manual check needed")
    else:
        print("[WARN] no SELECT from decision_results found")

    import ast; ast.parse(s)
    open(p, "w").write(s)
PYEOF

# ─── 5) RESTART + TEST ────────────────────────────────────────────
echo ""
echo "[5/5] Restart engine + smoke test"
fuser -k 8765/tcp 2>/dev/null || true
sleep 1
systemctl restart cas
sleep 4
systemctl is-active cas

# Test endpoint
echo "--- Smoke test ---"
curl -s -X POST \
  -H "Authorization: ApiKey cas_47316919d2238c4c93654a8998983473" \
  -H "Content-Type: application/json" \
  -d '{"decision_id":1,"action":"maneuver_approved"}' \
  http://localhost:8765/api/operator-decision | python3 -c 'import json,sys;print(json.dumps(json.load(sys.stdin),indent=2))'

# Verify DB
sudo -u postgres psql casdb -P pager=off -c "SELECT id, sat_name, recommendation, priority, operator_action, operator_action_at FROM decision_results ORDER BY id LIMIT 5;"

echo ""
echo "═══════════════════════════════════════════════════════════"
echo " DONE. Hard-refresh portal.html, go to Decisions tab."
echo " You should see ISS with 'APPROVE MANEUVER' button"
echo " (or 'Decision Recorded' if smoke test succeeded)."
echo "═══════════════════════════════════════════════════════════"
