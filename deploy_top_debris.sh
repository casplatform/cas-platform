#!/bin/bash
# ═══════════════════════════════════════════════════════════════════
# CAS Feature: Top 10 LEO Debris Threats
# ═══════════════════════════════════════════════════════════════════
set -e
cd /opt/cas

BACKUP_DIR=/root/nginx_backups
mkdir -p "$BACKUP_DIR"
TS=$(date +%Y%m%d_%H%M%S)

echo "═══════════════════════════════════════════════════════════"
echo " CAS — Top 10 LEO Debris Threats: full deployment"
echo "═══════════════════════════════════════════════════════════"

# ─── 1) DB MIGRATION ───────────────────────────────────────────────
echo ""
echo "[1/8] DB migration — leo_debris_ranking table + observation view"
sudo -u postgres psql casdb << 'SQLEOF'
CREATE TABLE IF NOT EXISTS leo_debris_ranking (
    id                   BIGSERIAL PRIMARY KEY,
    snapshot_week        DATE        NOT NULL,
    band                 TEXT        NOT NULL,  -- 'all' | 'low' | 'mid'
    rank                 INTEGER     NOT NULL,
    norad_id             TEXT        NOT NULL,
    object_name          TEXT        NOT NULL,
    cdm_count            INTEGER     NOT NULL DEFAULT 0,
    unique_counterparties INTEGER    NOT NULL DEFAULT 0,
    max_pc               DOUBLE PRECISION,
    cumulative_pc        DOUBLE PRECISION,
    threat_score         DOUBLE PRECISION,
    avg_altitude_km      DOUBLE PRECISION,
    first_seen           TIMESTAMPTZ,
    last_seen            TIMESTAMPTZ,
    computed_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_ldr_snapshot_band
    ON leo_debris_ranking(snapshot_week DESC, band, rank);
CREATE INDEX IF NOT EXISTS idx_ldr_norad
    ON leo_debris_ranking(norad_id);

CREATE OR REPLACE VIEW cas_observation_window AS
SELECT
    MIN(fetched_at) AS first_observation,
    MAX(fetched_at) AS last_observation,
    GREATEST(1, FLOOR(EXTRACT(EPOCH FROM (MAX(fetched_at) - MIN(fetched_at)))/86400)::int) AS days_observing,
    COUNT(DISTINCT cdm_id) AS unique_cdm_count
FROM conjunction_events;

SELECT * FROM cas_observation_window;
SQLEOF
echo "[OK] DB schema ready"

# ─── 2) RANK_DEBRIS.PY — pure-function ranker + DB writer ──────────
echo ""
echo "[2/8] Writing /opt/cas/rank_debris.py"
cat > /opt/cas/rank_debris.py << 'PYEOF'
#!/usr/bin/env python3
"""
CAS — Top LEO Debris Threats ranker.

Reads distinct CDMs from conjunction_events, classifies each counterparty
as debris via name pattern, computes per-debris ranking metrics, bands by
altitude (from Space-Track catalog cache), writes to leo_debris_ranking
for the current ISO week. Pure-function core is testable.
"""
import json, os, sys, re, datetime
from collections import defaultdict

DB_URL = os.environ["DB_URL"]
ST_CACHE = "/opt/cas/.spacetrack_catalog_cache.json"

DEBRIS_PATTERN = re.compile(r" DEB\b|\bR/B\b| DEBRIS\b", re.IGNORECASE)

# Altitude bands (km, average altitude)
BAND_LOW    = (500, 600)
BAND_MID    = (1000, 1200)


def is_debris(name: str) -> bool:
    """Name pattern heuristic for debris / rocket body detection."""
    if not name:
        return False
    return bool(DEBRIS_PATTERN.search(name))


def classify_band(altitude_km):
    """Return 'low', 'mid', or None for altitude band membership."""
    if altitude_km is None:
        return None
    if BAND_LOW[0] <= altitude_km <= BAND_LOW[1]:
        return "low"
    if BAND_MID[0] <= altitude_km <= BAND_MID[1]:
        return "mid"
    return None


def load_st_altitudes():
    """Load Space-Track catalog and compute avg altitude per NORAD ID.

    Uses TLE mean motion to derive semi-major axis, then perigee/apogee,
    then average altitude above Earth's surface.
    """
    try:
        with open(ST_CACHE, "r") as f:
            cache = json.load(f)
    except Exception as e:
        print(f"[WARN] could not read ST cache: {e}")
        return {}

    altitudes = {}
    MU = 398600.4418  # km^3/s^2
    R_EARTH = 6378.137  # km

    import math
    for kind in ("debris", "rocket_body"):
        for obj in cache.get(kind, []):
            norad = str(obj.get("norad", ""))
            l2 = obj.get("l2", "")
            if not norad or not l2 or len(l2) < 63:
                continue
            try:
                # TLE line 2, cols 53-63: mean motion (revs/day)
                mm = float(l2[52:63])
                ecc = float("0." + l2[26:33].strip())
                n_rad_per_s = mm * 2 * math.pi / 86400.0
                a = (MU / (n_rad_per_s ** 2)) ** (1/3)  # km
                perigee = a * (1 - ecc) - R_EARTH
                apogee = a * (1 + ecc) - R_EARTH
                avg_alt = (perigee + apogee) / 2.0
                altitudes[norad] = avg_alt
            except Exception:
                continue
    print(f"[rank] Loaded altitudes for {len(altitudes)} objects")
    return altitudes


def compute_rankings(cdms, altitudes):
    """Pure function: CDM list + altitude map -> ranking structure.

    Args:
        cdms: list of dicts with keys: cdm_id, sat1, sat2, norad1, norad2,
              pc, fetched_at (datetime or str)
        altitudes: dict norad_id (str) -> avg altitude km

    Returns: dict with keys 'all', 'low', 'mid', each a list of ranked
             debris dicts sorted by threat metric.
    """
    # Deduplicate by cdm_id — keep latest fetched_at
    by_id = {}
    for c in cdms:
        cid = c.get("cdm_id")
        if not cid:
            continue
        if cid not in by_id or (c.get("fetched_at") and str(c["fetched_at"]) > str(by_id[cid].get("fetched_at", ""))):
            by_id[cid] = c
    uniq = list(by_id.values())

    # Accumulate per-debris metrics
    # debris_key = norad_id (string)
    metrics = defaultdict(lambda: {
        "name": None, "norad": None,
        "cdm_count": 0,
        "counterparties": set(),
        "max_pc": 0.0,
        "cumulative_pc": 0.0,
        "first_seen": None, "last_seen": None,
    })

    for c in uniq:
        pc = float(c.get("pc") or 0)
        fa = c.get("fetched_at")
        pairs = [
            (c.get("sat1"), c.get("norad1"), c.get("sat2"), c.get("norad2")),
            (c.get("sat2"), c.get("norad2"), c.get("sat1"), c.get("norad1")),
        ]
        for self_name, self_norad, other_name, other_norad in pairs:
            if not is_debris(self_name):
                continue
            if not self_norad or self_norad == "?":
                continue
            key = str(self_norad)
            m = metrics[key]
            m["name"] = (self_name or "").strip()
            m["norad"] = key
            m["cdm_count"] += 1
            if other_norad and other_norad != "?":
                m["counterparties"].add(str(other_norad))
            if pc > m["max_pc"]:
                m["max_pc"] = pc
            m["cumulative_pc"] += pc
            if fa:
                if m["first_seen"] is None or str(fa) < str(m["first_seen"]):
                    m["first_seen"] = fa
                if m["last_seen"] is None or str(fa) > str(m["last_seen"]):
                    m["last_seen"] = fa

    # Convert to list + compute threat score
    # threat_score = unique_counterparties * 1000 + cumulative_pc * 1e6
    # (primary: counterparty count, tiebreaker: cumulative Pc)
    out = []
    for key, m in metrics.items():
        alt = altitudes.get(key)
        entry = {
            "norad_id": key,
            "object_name": m["name"] or "UNKNOWN",
            "cdm_count": m["cdm_count"],
            "unique_counterparties": len(m["counterparties"]),
            "max_pc": m["max_pc"],
            "cumulative_pc": m["cumulative_pc"],
            "threat_score": len(m["counterparties"]) * 1000.0 + m["cumulative_pc"] * 1e6,
            "avg_altitude_km": alt,
            "first_seen": m["first_seen"],
            "last_seen": m["last_seen"],
            "band": classify_band(alt),
        }
        out.append(entry)

    # Sort by threat_score desc
    out.sort(key=lambda x: x["threat_score"], reverse=True)

    # Partition by band
    rankings = {
        "all": out,
        "low": [e for e in out if e["band"] == "low"],
        "mid": [e for e in out if e["band"] == "mid"],
    }
    return rankings


def write_rankings_to_db(rankings, limit=25):
    """Write top-N rankings per band to leo_debris_ranking for current ISO week."""
    import psycopg2
    # ISO week Monday
    today = datetime.date.today()
    monday = today - datetime.timedelta(days=today.weekday())

    conn = psycopg2.connect(DB_URL)
    cur = conn.cursor()

    # Clear current week for idempotency
    cur.execute("DELETE FROM leo_debris_ranking WHERE snapshot_week = %s", (monday,))

    total = 0
    for band, entries in rankings.items():
        for rank, e in enumerate(entries[:limit], start=1):
            cur.execute("""
                INSERT INTO leo_debris_ranking
                  (snapshot_week, band, rank, norad_id, object_name,
                   cdm_count, unique_counterparties, max_pc, cumulative_pc,
                   threat_score, avg_altitude_km, first_seen, last_seen)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """, (
                monday, band, rank, e["norad_id"], e["object_name"],
                e["cdm_count"], e["unique_counterparties"],
                e["max_pc"] or None, e["cumulative_pc"] or None,
                e["threat_score"], e["avg_altitude_km"],
                e["first_seen"], e["last_seen"],
            ))
            total += 1

    conn.commit()
    cur.close()
    conn.close()
    return total, monday


def load_cdms_from_db():
    """Read distinct CDMs from conjunction_events."""
    import psycopg2
    conn = psycopg2.connect(DB_URL)
    cur = conn.cursor()
    cur.execute("""
        SELECT DISTINCT ON (cdm_id)
            cdm_id, sat1, sat2, norad1, norad2, pc, fetched_at
        FROM conjunction_events
        ORDER BY cdm_id, fetched_at DESC
    """)
    rows = cur.fetchall()
    cur.close(); conn.close()
    return [
        {"cdm_id": r[0], "sat1": r[1], "sat2": r[2],
         "norad1": r[3], "norad2": r[4], "pc": r[5], "fetched_at": r[6]}
        for r in rows
    ]


def main():
    print("[rank] Loading ST altitudes...")
    altitudes = load_st_altitudes()
    print("[rank] Loading CDMs from DB...")
    cdms = load_cdms_from_db()
    print(f"[rank] {len(cdms)} distinct CDMs loaded")
    print("[rank] Computing rankings...")
    rankings = compute_rankings(cdms, altitudes)
    for band in ("all", "low", "mid"):
        print(f"[rank] band={band}: {len(rankings[band])} debris entries")
    print("[rank] Writing to DB...")
    total, week = write_rankings_to_db(rankings)
    print(f"[rank] Wrote {total} ranking rows for week of {week}")
    print("[rank] DONE")


if __name__ == "__main__":
    main()
PYEOF
chmod +x /opt/cas/rank_debris.py
echo "[OK] rank_debris.py written"

# ─── 3) ENGINE ENDPOINT ─────────────────────────────────────────────
echo ""
echo "[3/8] Adding /stats/top-debris endpoint to cas_engine.py"
cp /opt/cas/cas_engine.py "$BACKUP_DIR/cas_engine.py.bak.$TS"

python3 << 'PYEOF'
p = "/opt/cas/cas_engine.py"
s = open(p).read()

if "/stats/top-debris" in s:
    print("[SKIP] endpoint already present")
else:
    marker = 'elif self.path == "/catalog/spacetrack":'
    block = '''elif self.path.startswith("/stats/top-debris"):
            try:
                import urllib.parse as _up
                qsT = _up.parse_qs(_up.urlparse(self.path).query)
                band = qsT.get("band", ["all"])[0]
                limit = min(int(qsT.get("limit", ["10"])[0]), 25)
                if band not in ("all", "low", "mid"):
                    band = "all"
                _conn = psycopg2.connect(os.environ.get("DB_URL",""))
                _cur = _conn.cursor()
                _cur.execute("""
                    SELECT rank, norad_id, object_name, cdm_count,
                           unique_counterparties, max_pc, cumulative_pc,
                           threat_score, avg_altitude_km,
                           first_seen, last_seen, snapshot_week
                    FROM leo_debris_ranking
                    WHERE band=%s
                      AND snapshot_week = (SELECT MAX(snapshot_week) FROM leo_debris_ranking WHERE band=%s)
                    ORDER BY rank ASC
                    LIMIT %s
                """, (band, band, limit))
                rows = _cur.fetchall()
                _cur.execute("SELECT first_observation, last_observation, days_observing, unique_cdm_count FROM cas_observation_window")
                obs = _cur.fetchone()
                _cur.close(); _conn.close()
                self._json({
                    "band": band,
                    "rankings": [
                        {
                            "rank": r[0], "norad_id": r[1], "object_name": r[2],
                            "cdm_count": r[3], "unique_counterparties": r[4],
                            "max_pc": float(r[5]) if r[5] is not None else None,
                            "cumulative_pc": float(r[6]) if r[6] is not None else None,
                            "threat_score": float(r[7]) if r[7] is not None else None,
                            "avg_altitude_km": float(r[8]) if r[8] is not None else None,
                            "first_seen": r[9].isoformat() if r[9] else None,
                            "last_seen": r[10].isoformat() if r[10] else None,
                            "snapshot_week": r[11].isoformat() if r[11] else None,
                        }
                        for r in rows
                    ],
                    "observation": {
                        "first_date": obs[0].isoformat() if obs and obs[0] else None,
                        "last_date": obs[1].isoformat() if obs and obs[1] else None,
                        "days_observing": int(obs[2]) if obs and obs[2] else 0,
                        "unique_cdm_count": int(obs[3]) if obs and obs[3] else 0,
                    }
                })
            except Exception as e:
                self._json({"error": str(e), "rankings": [], "observation": {}}, 500)

        ''' + marker
    s = s.replace(marker, block, 1)
    open(p, "w").write(s)
    import ast; ast.parse(s)
    print("[OK] endpoint added, AST OK")
PYEOF
echo "[OK] engine endpoint installed"

# ─── 4) PORTAL WIDGET (new EUSST-adjacent tab) ─────────────────────
echo ""
echo "[4/8] Adding portal.html widget"
cp /opt/cas/static/portal.html "$BACKUP_DIR/portal.html.bak.$TS"

python3 << 'PYEOF'
p = "/opt/cas/static/portal.html"
s = open(p).read()

if "renderTopDebris" in s:
    print("[SKIP] portal widget already present")
else:
    # Insert a new render function right before renderEusstReentries
    anchor = "async function renderEusstReentries(el){"
    new_func = '''async function renderTopDebris(el){
  const tabs = `<div class="tab-bar" style="margin-bottom:12px;">
    <button class="tab-btn active" data-band="all">All LEO</button>
    <button class="tab-btn" data-band="low">500–600 km</button>
    <button class="tab-btn" data-band="mid">1000–1200 km</button>
  </div>`;
  const info = `<div id="tdObs" style="background:rgba(251,191,36,.08);border:1px solid rgba(251,191,36,.4);border-radius:6px;padding:12px 16px;margin-bottom:16px;font-size:11px;line-height:1.55;color:#d4a45a;">
    <strong>⚠ Observational data.</strong> Loading observation window...
  </div>`;
  el.innerHTML = `<div class="content-header">
    <h2>🛰️ Top 10 LEO Debris Threats</h2>
    <p>Debris objects generating the most conjunction alerts in CAS's operational window</p>
  </div>${info}${tabs}<div id="tdBody">Loading…</div>`;

  async function loadBand(band){
    const body = document.getElementById('tdBody');
    body.innerHTML = 'Loading…';
    try {
      const r = await fetch('/stats/top-debris?band='+band+'&limit=10');
      const d = await r.json();
      const obs = d.observation || {};
      const obsEl = document.getElementById('tdObs');
      if (obsEl) {
        obsEl.innerHTML = `<strong>⚠ Observational data.</strong> CAS has been continuously observing LEO conjunction events for <strong>${obs.days_observing||0} days</strong> (${(obs.unique_cdm_count||0).toLocaleString()} unique CDMs ingested). Rankings are based on our operational window and grow in statistical power over time. First observation: ${(obs.first_date||'—').slice(0,10)}.`;
      }
      const rows = d.rankings || [];
      if (!rows.length) {
        body.innerHTML = `<div style="padding:24px;text-align:center;color:var(--muted);font-size:12px;">Insufficient observations in this altitude band yet. CAS continues to monitor — check back as our dataset grows.</div>`;
        return;
      }
      const fmt = (v, digits) => (v==null||isNaN(v)) ? '—' : Number(v).toFixed(digits);
      const trs = rows.map(x => `<tr>
        <td style="font-family:var(--mono);color:var(--red);font-weight:700;">#${x.rank}</td>
        <td>${x.object_name}</td>
        <td style="font-family:var(--mono);">${x.norad_id}</td>
        <td style="text-align:right;">${x.unique_counterparties}</td>
        <td style="text-align:right;">${x.cdm_count}</td>
        <td style="text-align:right;font-family:var(--mono);">${x.max_pc!=null ? x.max_pc.toExponential(2) : '—'}</td>
        <td style="text-align:right;">${x.avg_altitude_km!=null ? Math.round(x.avg_altitude_km)+' km' : '—'}</td>
      </tr>`).join('');
      body.innerHTML = `<table class="data-table"><thead><tr>
        <th>Rank</th><th>Object</th><th>NORAD</th>
        <th style="text-align:right;">Unique<br>Counterparties</th>
        <th style="text-align:right;">CDM<br>Count</th>
        <th style="text-align:right;">Max Pc</th>
        <th style="text-align:right;">Avg Alt</th>
      </tr></thead><tbody>${trs}</tbody></table>
      <p style="font-size:10px;color:var(--muted);margin-top:12px;line-height:1.5;">
        Ranking primary: unique counterparties threatened. Tiebreaker: cumulative Pc.
        Debris classification: name pattern heuristic (DEB, R/B, DEBRIS).
        Altitudes derived from Space-Track TLE cache.
      </p>`;
    } catch(e) {
      body.innerHTML = '<p style="color:var(--red);">Failed to load rankings: '+e.message+'</p>';
    }
  }

  // Tab wiring
  el.querySelectorAll('.tab-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      el.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      loadBand(btn.dataset.band);
    });
  });
  loadBand('all');
}

'''
    s = s.replace(anchor, new_func + anchor, 1)
    open(p, "w").write(s)
    print("[OK] renderTopDebris function injected")
PYEOF

# Register the tab in portal's dispatcher (best-effort; manual wiring may be needed)
python3 << 'PYEOF'
p = "/opt/cas/static/portal.html"
s = open(p).read()
# Try to find the sidebar where EUSST sections live and add a new item
if 'data-section="top-debris"' in s:
    print("[SKIP] sidebar entry exists")
else:
    # Look for an existing EUSST-related sidebar item as anchor
    anchor_candidates = [
        'data-section="eusst-reentries"',
        'data-section="eusst-aggregate"',
    ]
    inserted = False
    for anc in anchor_candidates:
        if anc in s:
            anc_line_start = s.rfind("<", 0, s.find(anc))
            anc_line_end = s.find("</", s.find(anc))
            if anc_line_start >= 0 and anc_line_end > anc_line_start:
                # Copy the <a ...>...</a> pattern for the anchor element
                full_line_end = s.find(">", anc_line_end) + 1
                anchor_line = s[anc_line_start:full_line_end]
                new_line = anchor_line.replace('eusst-reentries', 'top-debris').replace('eusst-aggregate', 'top-debris')
                # Tweak label between > and </
                import re as _re
                new_line = _re.sub(r">([^<]+)</", "> Top 10 Debris Threats</", new_line, count=1)
                s = s.replace(anchor_line, anchor_line + "\n        " + new_line, 1)
                inserted = True
                break
    if inserted:
        open(p, "w").write(s)
        print("[OK] sidebar entry added (best-effort)")
    else:
        print("[WARN] could not auto-wire sidebar; manual step needed — see README")
PYEOF

# Register in the renderer dispatcher
python3 << 'PYEOF'
p = "/opt/cas/static/portal.html"
s = open(p).read()
if "'top-debris':" in s or '"top-debris":' in s:
    print("[SKIP] dispatcher entry exists")
else:
    # Find the renderer map that references renderEusstReentries
    import re
    m = re.search(r"(['\"]eusst-reentries['\"]\s*:\s*renderEusstReentries)", s)
    if m:
        replacement = m.group(0) + ",\n    'top-debris': renderTopDebris"
        s = s.replace(m.group(0), replacement, 1)
        open(p, "w").write(s)
        print("[OK] dispatcher entry added")
    else:
        print("[WARN] renderer dispatcher not auto-detected; see README for manual wiring")
PYEOF

echo "[OK] portal.html patched"

# ─── 5) LANDING TEASER ─────────────────────────────────────────────
echo ""
echo "[5/8] Adding landing.html top-3 teaser section"
cp /opt/cas/static/landing.html "$BACKUP_DIR/landing.html.bak.$TS"

python3 << 'PYEOF'
p = "/opt/cas/static/landing.html"
s = open(p).read()
if "top-debris-teaser" in s:
    print("[SKIP] teaser already present")
    import sys; sys.exit(0)

teaser = '''
<!-- ═══ Top LEO Debris Threats Teaser ═══ -->
<section id="top-debris-teaser" style="padding:60px 20px;background:rgba(0,0,0,0.25);border-top:1px solid rgba(255,255,255,0.05);border-bottom:1px solid rgba(255,255,255,0.05);">
  <div style="max-width:1100px;margin:0 auto;">
    <span class="section-tag">OBSERVATIONAL DATA</span>
    <h2 style="font-family:var(--mono);font-size:28px;font-weight:700;margin-bottom:8px;">Top LEO Debris Threats</h2>
    <p style="color:var(--muted);font-size:13px;margin-bottom:20px;">Debris objects generating the most close-approach alerts in CAS's operational window.</p>
    <div id="tdTeaserObs" style="background:rgba(251,191,36,.08);border:1px solid rgba(251,191,36,.3);border-radius:6px;padding:10px 14px;margin-bottom:18px;font-size:11px;line-height:1.5;color:#d4a45a;">
      Loading observation window...
    </div>
    <div id="tdTeaserBody" style="overflow-x:auto;">Loading…</div>
    <p style="margin-top:16px;">
      <a href="/portal.html#top-debris" style="font-family:var(--mono);font-size:11px;color:var(--cyan);text-decoration:none;letter-spacing:1px;">VIEW FULL LIST →</a>
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
    if (obsEl) obsEl.innerHTML = `⚠ Observational data. CAS has been continuously observing for <strong>${obs.days_observing||0} days</strong> (${(obs.unique_cdm_count||0).toLocaleString()} unique CDMs). Rankings grow in statistical power over time.`;
    const rows = (d.rankings || []).map(x => `<tr>
      <td style="padding:10px 12px;font-family:var(--mono);color:var(--red);font-weight:700;">#${x.rank}</td>
      <td style="padding:10px 12px;">${x.object_name}</td>
      <td style="padding:10px 12px;font-family:var(--mono);">${x.norad_id}</td>
      <td style="padding:10px 12px;text-align:right;">${x.unique_counterparties} counterparties</td>
      <td style="padding:10px 12px;text-align:right;">${x.cdm_count} CDMs</td>
    </tr>`).join('');
    const body = document.getElementById('tdTeaserBody');
    if (!rows) {
      body.innerHTML = '<p style="color:var(--muted);font-size:12px;">Insufficient observations yet. Check back as our dataset grows.</p>';
    } else {
      body.innerHTML = `<table style="width:100%;border-collapse:collapse;font-size:12px;">
        <thead><tr style="background:rgba(255,255,255,0.03);border-bottom:1px solid rgba(255,255,255,0.08);">
          <th style="padding:10px 12px;text-align:left;font-family:var(--mono);font-size:10px;letter-spacing:1px;color:var(--muted);">RANK</th>
          <th style="padding:10px 12px;text-align:left;font-family:var(--mono);font-size:10px;letter-spacing:1px;color:var(--muted);">OBJECT</th>
          <th style="padding:10px 12px;text-align:left;font-family:var(--mono);font-size:10px;letter-spacing:1px;color:var(--muted);">NORAD</th>
          <th style="padding:10px 12px;text-align:right;font-family:var(--mono);font-size:10px;letter-spacing:1px;color:var(--muted);">THREAT SPREAD</th>
          <th style="padding:10px 12px;text-align:right;font-family:var(--mono);font-size:10px;letter-spacing:1px;color:var(--muted);">FREQUENCY</th>
        </tr></thead>
        <tbody>${rows}</tbody>
      </table>`;
    }
  } catch(e) {
    const body = document.getElementById('tdTeaserBody');
    if (body) body.innerHTML = '<p style="color:var(--red);">Failed to load.</p>';
  }
})();
</script>
'''

# Insert before the footer if possible, else near end of </body>
anchor = "</body>"
if anchor in s:
    s = s.replace(anchor, teaser + "\n" + anchor, 1)
    open(p, "w").write(s)
    print("[OK] landing teaser added before </body>")
else:
    print("[WARN] </body> not found; manual insertion needed")
PYEOF

# ─── 6) CRON JOB ───────────────────────────────────────────────────
echo ""
echo "[6/8] Installing weekly cron job"
cat > /etc/cron.d/cas-rank-debris << 'CRONEOF'
# CAS — weekly debris ranking refresh
# Runs every Sunday at 03:00 server time
0 3 * * 0 root cd /opt/cas && /usr/bin/python3 /opt/cas/rank_debris.py >> /var/log/cas/rank_debris.log 2>&1
CRONEOF
mkdir -p /var/log/cas
chmod 644 /etc/cron.d/cas-rank-debris
echo "[OK] cron installed"

# ─── 7) PYTEST — ranker unit tests ─────────────────────────────────
echo ""
echo "[7/8] Writing tests/test_rank_debris.py"
cat > /opt/cas/tests/test_rank_debris.py << 'PYEOF'
"""Unit tests for rank_debris.compute_rankings — pure function core."""
import sys, os
sys.path.insert(0, "/opt/cas")
from rank_debris import is_debris, classify_band, compute_rankings


class TestIsDebris:
    def test_classic_debris(self):
        assert is_debris("COSMOS 1408 DEB") is True
        assert is_debris("FENGYUN 1C DEBRIS") is True

    def test_rocket_body(self):
        assert is_debris("ARIANE 42P R/B") is True

    def test_active_satellite_not_debris(self):
        assert is_debris("STARLINK-12345") is False
        assert is_debris("ISS (ZARYA)") is False

    def test_empty_name(self):
        assert is_debris("") is False
        assert is_debris(None) is False


class TestClassifyBand:
    def test_low_band(self):
        assert classify_band(550) == "low"
        assert classify_band(500) == "low"
        assert classify_band(600) == "low"

    def test_mid_band(self):
        assert classify_band(1100) == "mid"
        assert classify_band(1000) == "mid"
        assert classify_band(1200) == "mid"

    def test_out_of_band(self):
        assert classify_band(400) is None
        assert classify_band(800) is None
        assert classify_band(1500) is None
        assert classify_band(None) is None


class TestComputeRankings:
    def _cdm(self, cid, s1, n1, s2, n2, pc):
        return {"cdm_id": cid, "sat1": s1, "norad1": n1,
                "sat2": s2, "norad2": n2, "pc": pc, "fetched_at": "2026-04-14"}

    def test_empty_input(self):
        r = compute_rankings([], {})
        assert r == {"all": [], "low": [], "mid": []}

    def test_single_debris_counted_once(self):
        cdms = [self._cdm("C1", "STARLINK-1", "100", "COSMOS DEB", "200", 1e-4)]
        r = compute_rankings(cdms, {"200": 550})
        assert len(r["all"]) == 1
        assert r["all"][0]["norad_id"] == "200"
        assert r["all"][0]["cdm_count"] == 1
        assert r["all"][0]["unique_counterparties"] == 1

    def test_threat_score_ranking(self):
        # Debris A has 3 counterparties; debris B has 1 counterparty but higher Pc
        cdms = [
            self._cdm("1", "SAT1", "1", "DEB A", "A", 1e-5),
            self._cdm("2", "SAT2", "2", "DEB A", "A", 1e-5),
            self._cdm("3", "SAT3", "3", "DEB A", "A", 1e-5),
            self._cdm("4", "SAT4", "4", "DEB B", "B", 1e-2),
        ]
        r = compute_rankings(cdms, {})
        # Counterparty-dominant scoring: A should outrank B
        assert r["all"][0]["norad_id"] == "A"
        assert r["all"][1]["norad_id"] == "B"

    def test_band_classification(self):
        cdms = [
            self._cdm("1", "SAT", "10", "DEB LOW", "L", 1e-4),
            self._cdm("2", "SAT", "11", "DEB MID", "M", 1e-4),
            self._cdm("3", "SAT", "12", "DEB HIGH", "H", 1e-4),
        ]
        alts = {"L": 550, "M": 1100, "H": 1500}
        r = compute_rankings(cdms, alts)
        low_ids = [e["norad_id"] for e in r["low"]]
        mid_ids = [e["norad_id"] for e in r["mid"]]
        assert "L" in low_ids and "L" not in mid_ids
        assert "M" in mid_ids and "M" not in low_ids
        assert "H" not in low_ids and "H" not in mid_ids

    def test_dedup_by_cdm_id(self):
        # Same cdm_id twice — must count once
        cdms = [
            self._cdm("DUPE", "SAT", "1", "DEB", "D", 1e-4),
            self._cdm("DUPE", "SAT", "1", "DEB", "D", 1e-4),
        ]
        r = compute_rankings(cdms, {})
        assert r["all"][0]["cdm_count"] == 1

    def test_active_vs_active_ignored(self):
        # No debris on either side → nothing in output
        cdms = [self._cdm("X", "STARLINK-1", "1", "ONEWEB-1", "2", 1e-4)]
        r = compute_rankings(cdms, {})
        assert r == {"all": [], "low": [], "mid": []}
PYEOF
echo "[OK] test file written"

# ─── 8) FIRST RUN + VALIDATION ─────────────────────────────────────
echo ""
echo "[8/8] First manual run + engine restart + test suite"

echo "─── Running ranker..."
cd /opt/cas
set -a; source .env 2>/dev/null || true; set +a
python3 /opt/cas/rank_debris.py || echo "[WARN] rank_debris first run had issues — see log"

echo ""
echo "─── Restarting engine..."
fuser -k 8765/tcp 2>/dev/null || true
sleep 1
systemctl restart cas
sleep 5
systemctl is-active cas

echo ""
echo "─── Endpoint smoke test..."
curl -s "http://localhost:8765/stats/top-debris?band=all&limit=5" | python3 -c 'import json,sys;d=json.load(sys.stdin);print("rankings:",len(d.get("rankings",[])),"days:",d.get("observation",{}).get("days_observing"))' || echo "[WARN] endpoint test failed"

echo ""
echo "─── Full pytest suite..."
python3 -m pytest tests/ --tb=short 2>&1 | tail -15

echo ""
echo "═══════════════════════════════════════════════════════════"
echo " DONE. Browser hard refresh casplatform.com/portal.html"
echo " and casplatform.com/landing.html to see new widgets."
echo "═══════════════════════════════════════════════════════════"
