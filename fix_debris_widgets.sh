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
# Three surgical fixes for Top 10 LEO Debris Threats widget
set -e
TS=$(date +%Y%m%d_%H%M%S)
BACKUP=/root/nginx_backups
mkdir -p "$BACKUP"

echo "[1/3] Landing — relocate teaser + dark theme restyle"
cp /opt/cas/static/landing.html "$BACKUP/landing.html.bak.$TS"

python3 << 'PYEOF'
p = "/opt/cas/static/landing.html"
s = open(p).read()

# Step 1: Remove existing teaser block (from <!-- Top LEO Debris Threats Teaser --> to </script>)
import re
old = re.search(
    r"\n<!-- ═══ Top LEO Debris Threats Teaser ═══ -->.*?</script>\s*",
    s, re.DOTALL
)
if not old:
    print("[WARN] existing teaser block not found; skipping removal")
else:
    s = s[:old.start()] + s[old.end():]
    print("[OK] old teaser removed")

# Step 2: Build new dark-theme teaser
new_teaser = '''
<!-- ═══ Top LEO Debris Threats Teaser ═══ -->
<section id="top-debris-teaser" style="padding:80px 20px;background:linear-gradient(180deg, rgba(10,21,37,0.4) 0%, rgba(6,12,22,0.8) 100%);border-top:1px solid rgba(0,201,219,0.08);">
  <div style="max-width:1100px;margin:0 auto;">
    <span style="font-family:var(--mono);font-size:10px;color:var(--cyan);letter-spacing:3px;text-transform:uppercase;margin-bottom:14px;display:block;">OBSERVATIONAL DATA</span>
    <h2 style="font-family:var(--mono);font-size:32px;font-weight:700;margin-bottom:12px;color:#fff;">Top LEO Debris Threats</h2>
    <p style="color:rgba(255,255,255,0.55);font-size:14px;margin-bottom:24px;max-width:720px;line-height:1.6;">Debris objects generating the most close-approach alerts in CAS's operational window. Continuously updated as our ingestion grows.</p>
    <div id="tdTeaserObs" style="background:rgba(251,191,36,.06);border:1px solid rgba(251,191,36,.25);border-radius:6px;padding:12px 16px;margin-bottom:20px;font-size:11px;line-height:1.6;color:#d4a45a;font-family:var(--mono);">
      Loading observation window...
    </div>
    <div id="tdTeaserBody" style="overflow-x:auto;background:rgba(15,29,48,0.5);border:1px solid rgba(255,255,255,0.06);border-radius:6px;">Loading…</div>
    <p style="margin-top:20px;">
      <a href="/portal.html" style="font-family:var(--mono);font-size:11px;color:var(--cyan);text-decoration:none;letter-spacing:1.5px;border:1px solid rgba(0,201,219,0.3);padding:10px 18px;border-radius:4px;display:inline-block;">VIEW FULL LIST IN PORTAL →</a>
    </p>
  </div>
</section>
<script>
(async function(){
  try {
    const r = await fetch('/stats/top-debris?band=all&limit=3');
    const d = await r.json();
    const obs = d.observation || {};
    const obsEl = document.getElementById('tdTeaserObs');
    if (obsEl) obsEl.innerHTML = `⚠ Observational data. CAS has been continuously observing for <strong style="color:#f5c074;">${obs.days_observing||0} days</strong> (${(obs.unique_cdm_count||0).toLocaleString()} unique CDMs). Rankings grow in statistical power over time.`;
    const rows = (d.rankings || []).map(x => `<tr style="border-bottom:1px solid rgba(255,255,255,0.04);">
      <td style="padding:14px 18px;font-family:var(--mono);color:#ef4444;font-weight:700;font-size:13px;">#${x.rank}</td>
      <td style="padding:14px 18px;color:#fff;font-size:13px;">${x.object_name}</td>
      <td style="padding:14px 18px;font-family:var(--mono);color:rgba(255,255,255,0.6);font-size:12px;">${x.norad_id}</td>
      <td style="padding:14px 18px;text-align:right;color:rgba(255,255,255,0.8);font-size:12px;">${x.unique_counterparties} counterparties</td>
      <td style="padding:14px 18px;text-align:right;color:rgba(255,255,255,0.8);font-size:12px;">${x.cdm_count} CDMs</td>
    </tr>`).join('');
    const body = document.getElementById('tdTeaserBody');
    if (!rows) {
      body.innerHTML = '<p style="color:rgba(255,255,255,0.4);font-size:12px;padding:24px;text-align:center;">Insufficient observations yet. Check back as our dataset grows.</p>';
    } else {
      body.innerHTML = `<table style="width:100%;border-collapse:collapse;font-size:12px;">
        <thead><tr style="background:rgba(255,255,255,0.02);border-bottom:1px solid rgba(0,201,219,0.15);">
          <th style="padding:12px 18px;text-align:left;font-family:var(--mono);font-size:9px;letter-spacing:2px;color:var(--cyan);">RANK</th>
          <th style="padding:12px 18px;text-align:left;font-family:var(--mono);font-size:9px;letter-spacing:2px;color:var(--cyan);">OBJECT</th>
          <th style="padding:12px 18px;text-align:left;font-family:var(--mono);font-size:9px;letter-spacing:2px;color:var(--cyan);">NORAD</th>
          <th style="padding:12px 18px;text-align:right;font-family:var(--mono);font-size:9px;letter-spacing:2px;color:var(--cyan);">THREAT SPREAD</th>
          <th style="padding:12px 18px;text-align:right;font-family:var(--mono);font-size:9px;letter-spacing:2px;color:var(--cyan);">FREQUENCY</th>
        </tr></thead>
        <tbody>${rows}</tbody>
      </table>`;
    }
  } catch(e) {
    const body = document.getElementById('tdTeaserBody');
    if (body) body.innerHTML = '<p style="color:#ef4444;font-size:12px;padding:24px;">Failed to load.</p>';
  }
})();
</script>
'''

# Step 3: Insert BEFORE <footer> (not before </body>)
footer_idx = s.find("<footer>")
if footer_idx < 0:
    print("[FATAL] <footer> not found")
    import sys; sys.exit(1)
s = s[:footer_idx] + new_teaser + "\n" + s[footer_idx:]
open(p, "w").write(s)
print("[OK] new teaser inserted before <footer>")
PYEOF

echo ""
echo "[2/3] Portal — wire dispatcher + sidebar item"
cp /opt/cas/static/portal.html "$BACKUP/portal.html.bak.$TS"

python3 << 'PYEOF'
p = "/opt/cas/static/portal.html"
s = open(p).read()

# A) Add dispatcher entry if missing
disp_old = "else if (name === 'eusst_fragmentations') renderEusstFragmentations(content);"
disp_new = "else if (name === 'eusst_fragmentations') renderEusstFragmentations(content);\n  else if (name === 'top_debris') renderTopDebris(content);"
if "name === 'top_debris'" in s:
    print("[SKIP] dispatcher entry already present")
else:
    assert disp_old in s, "dispatcher anchor not found"
    s = s.replace(disp_old, disp_new, 1)
    print("[OK] dispatcher entry added")

# B) Add sidebar item after Fragmentations
sb_old = '<div class="sidebar-item" onclick="showSection(\'eusst_fragmentations\')">Fragmentations</div>'
sb_new = sb_old + '\n          <div class="sidebar-item" onclick="showSection(\'top_debris\')">Top 10 Debris Threats</div>'
if "showSection('top_debris')" in s:
    print("[SKIP] sidebar item already present")
else:
    assert sb_old in s, "sidebar anchor not found"
    s = s.replace(sb_old, sb_new, 1)
    print("[OK] sidebar item added")

open(p, "w").write(s)
PYEOF

echo ""
echo "[3/3] Verify — grep wired elements"
grep -n "top_debris\|top-debris" /opt/cas/static/portal.html | head -5
grep -n "top-debris-teaser\|<footer>" /opt/cas/static/landing.html | head -5

echo ""
echo "DONE. Hard-refresh casplatform.com (landing) and portal.html (sidebar)."
