#!/usr/bin/env python3

# NOTE (2026-08-17): one-off setup tool, not part of any scheduled run.
# The CREATE TABLE statements below are historical: those tables are in
# the Alembic baseline now. Schema changes belong in migrations/ -- if
# this script is ever revived, take the DDL out first.
"""
CAS — Business Directory Feature
DB table + seed from Space-Track + engine endpoint + portal tab
"""

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
import sys as _guard_sys
print("REFUSING TO RUN: this is a retired one-shot production patch. "
      "Use /opt/cas/scripts/deploy.sh instead.", file=_guard_sys.stderr)
raise SystemExit(2)
# ────────────────────────────────────────────────────────────────────────────
import os, json

PORTAL = "/opt/cas/static/portal.html"
ENGINE = "/opt/cas/cas_engine.py"
TS = __import__("datetime").datetime.now().strftime("%Y%m%d_%H%M%S")
BACKUP = "/root/nginx_backups"
os.makedirs(BACKUP, exist_ok=True)
os.system(f"cp {PORTAL} {BACKUP}/portal.html.bak.bizdir.{TS}")
os.system(f"cp {ENGINE} {BACKUP}/cas_engine.py.bak.bizdir.{TS}")

print("═══ BUSINESS DIRECTORY DEPLOYMENT ═══\n")

# ═══════════════════════════════════════════════════
# 1) DB TABLE + SEED DATA
# ═══════════════════════════════════════════════════
print("[1/4] Database setup + seed...")

import subprocess
sql = """
-- Create table
CREATE TABLE IF NOT EXISTS business_directory (
    id SERIAL PRIMARY KEY,
    name VARCHAR(255) NOT NULL,
    country_code VARCHAR(10),
    country_name VARCHAR(100),
    category VARCHAR(50) DEFAULT 'operator',
    satellite_count INTEGER DEFAULT 0,
    constellation VARCHAR(100),
    website VARCHAR(255),
    contact_email VARCHAR(255),
    description TEXT,
    hq_location VARCHAR(255),
    founded VARCHAR(20),
    stock_ticker VARCHAR(30),
    data_source VARCHAR(50) DEFAULT 'manual',
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_bizdir_country ON business_directory(country_code);
CREATE INDEX IF NOT EXISTS idx_bizdir_category ON business_directory(category);

-- Grant permissions
GRANT SELECT, INSERT, UPDATE, DELETE ON business_directory TO cas;
GRANT USAGE, SELECT ON SEQUENCE business_directory_id_seq TO cas;

-- Seed data (idempotent via ON CONFLICT)
ALTER TABLE business_directory ADD CONSTRAINT uq_bizdir_name UNIQUE (name);

INSERT INTO business_directory (name, country_code, country_name, category, satellite_count, constellation, website, contact_email, description, hq_location, stock_ticker) VALUES
-- Mega-constellation operators
('SpaceX', 'US', 'United States', 'operator', 6000, 'Starlink', 'https://www.spacex.com', 'sales@spacex.com', 'Designs, manufactures and launches rockets and spacecraft. Operates Starlink broadband constellation.', 'Hawthorne, California', NULL),
('OneWeb', 'GB', 'United Kingdom', 'operator', 648, 'OneWeb', 'https://oneweb.net', 'info@oneweb.net', 'Global communications company building a LEO satellite constellation for broadband connectivity.', 'London, UK', NULL),
('Amazon (Project Kuiper)', 'US', 'United States', 'operator', 88, 'Kuiper', 'https://www.aboutamazon.com/what-we-do/devices-services/project-kuiper', 'amazon-pr@amazon.com', 'LEO broadband constellation under development by Amazon.', 'Seattle, Washington', 'NASDAQ:AMZN'),
('Eutelsat Group', 'FR', 'France', 'operator', 713, 'Eutelsat/OneWeb', 'https://www.eutelsat.com', 'csc@eutelsat.com', 'European satellite telecommunications operator. Merged with OneWeb in 2023.', 'Issy-les-Moulineaux, France', 'EURONEXT:ETL'),
('SES S.A.', 'LU', 'Luxembourg', 'operator', 239, 'O3b mPOWER', 'https://www.ses.com', NULL, 'Global satellite operator providing video and data connectivity worldwide.', 'Betzdorf, Luxembourg', 'OTC:SGBAF'),
('Iridium Communications', 'US', 'United States', 'operator', 106, 'Iridium NEXT', 'https://www.iridium.com', 'sales@iridium.com', 'Operates the Iridium satellite constellation providing L-band voice and data coverage.', 'McLean, Virginia', 'NASDAQ:IRDM'),
('Telesat', 'CA', 'Canada', 'operator', 29, 'Lightspeed', 'https://www.telesat.com', 'info@telesat.com', 'Canadian satellite communications company developing Lightspeed LEO constellation.', 'Ottawa, Canada', 'NASDAQ:TSAT'),
('Globalstar', 'US', 'United States', 'operator', 87, 'Globalstar', 'https://www.globalstar.com', 'cgsales@globalstar.com', 'Provides mobile satellite voice and data services via LEO constellation.', 'Covington, Louisiana', 'NASDAQ:GSAT'),
('Orbcomm', 'US', 'United States', 'operator', 61, 'Orbcomm', 'https://www.orbcomm.com', 'Customer.Care@orbcomm.com', 'Industrial IoT solutions provider operating LEO M2M constellation.', 'Rochelle Park, New Jersey', 'NASDAQ:ORBC'),
-- Earth observation
('Planet Labs', 'US', 'United States', 'operator', 159, 'Flock/SkySat', 'https://www.planet.com', 'support@planet.com', 'Earth observation company operating the largest constellation of imaging satellites.', 'San Francisco, California', 'NYSE:PL'),
('ICEYE', 'FI', 'Finland', 'operator', 26, 'ICEYE SAR', 'https://www.iceye.com', 'press@iceye.com', 'Finnish microsatellite company operating SAR imaging constellation.', 'Helsinki, Finland', NULL),
('Spire Global', 'US', 'United States', 'operator', 73, 'LEMUR', 'https://spire.com', NULL, 'Space-based data analytics company. Weather, maritime, and aviation tracking.', 'San Francisco, California', 'NYSE:SPIR'),
('HawkEye 360', 'US', 'United States', 'operator', 37, 'HawkEye', 'https://www.he360.com', 'info@he360.com', 'Radio frequency geospatial analytics company.', 'Herndon, Virginia', NULL),
('BlackSky', 'US', 'United States', 'operator', 16, 'BlackSky', 'https://www.blacksky.com', 'info@blacksky.com', 'Real-time geospatial intelligence and global monitoring.', 'Herndon, Virginia', 'NYSE:BKSY'),
('Satellogic', 'AR', 'Argentina', 'operator', 34, 'NewSat', 'https://satellogic.com', 'info@satellogic.com', 'Sub-meter resolution Earth observation constellation.', 'Buenos Aires, Argentina', 'NASDAQ:SATL'),
-- European operators & agencies
('Airbus Defence and Space', 'FR', 'France', 'manufacturer', 25, NULL, 'https://www.airbus.com/space', 'mktg-spacesystems@airbus.com', 'Major European spacecraft manufacturer and operator.', 'Toulouse, France', 'OTC:EADSY'),
('OHB SE', 'DE', 'Germany', 'manufacturer', 12, NULL, 'https://www.ohb.de', 'info@ohb.de', 'German space and technology company. Builds satellites for institutional customers.', 'Bremen, Germany', 'ETR:OHB'),
('D-Orbit', 'IT', 'Italy', 'operator', 8, 'ION', 'https://www.dorbit.space', 'info@dorbit.space', 'Space logistics and transportation company. ION satellite carrier.', 'Fino Mornasco, Italy', NULL),
('European Space Agency', 'EU', 'Europe', 'agency', 113, NULL, 'https://www.esa.int', 'media@esa.int', 'Intergovernmental organisation dedicated to space exploration and research.', 'Paris, France', NULL),
('CNES', 'FR', 'France', 'agency', 59, NULL, 'https://cnes.fr', 'contact@cnes.fr', 'French government space agency.', 'Paris, France', NULL),
('DLR', 'DE', 'Germany', 'agency', 20, NULL, 'https://www.dlr.de', 'contact-dlr@dlr.de', 'German Aerospace Center.', 'Cologne, Germany', NULL),
('ASI', 'IT', 'Italy', 'agency', 38, NULL, 'https://www.asi.it', 'urp@asi.it', 'Italian Space Agency.', 'Rome, Italy', NULL),
('POLSA', 'PL', 'Poland', 'agency', 2, NULL, 'https://polsa.gov.pl', NULL, 'Polish Space Agency.', 'Warsaw, Poland', NULL),
-- Asian operators
('ISRO', 'IN', 'India', 'agency', 100, NULL, 'https://www.isro.gov.in', 'isropr@isro.gov.in', 'Indian Space Research Organisation.', 'Bengaluru, India', NULL),
('JAXA', 'JP', 'Japan', 'agency', 92, NULL, 'https://www.jaxa.jp', 'ko-ho@chofu.jaxa.jp', 'Japan Aerospace Exploration Agency.', 'Chofu, Tokyo', NULL),
('CNSA', 'CN', 'China', 'agency', 91, NULL, 'https://www.cnsa.gov.cn', 'webmaster@cnsa.gov.cn', 'China National Space Administration.', 'Beijing, China', NULL),
('Shanghai Spacecom (GeeSpace)', 'CN', 'China', 'operator', 113, 'Qianfan/G60', 'https://www.chinaerospace.com', 'info@chinaerospace.com', 'Chinese LEO broadband constellation operator (G60/Qianfan).', 'Shanghai, China', NULL),
('SKY Perfect JSAT', 'JP', 'Japan', 'operator', 27, NULL, NULL, NULL, 'Japanese satellite communications operator.', 'Tokyo, Japan', 'TSE:9412'),
-- Government / Defense
('NASA', 'US', 'United States', 'agency', 248, NULL, 'https://www.nasa.gov', 'public-inquiries@hq.nasa.gov', 'National Aeronautics and Space Administration.', 'Washington, DC', NULL),
('US Space Force', 'US', 'United States', 'agency', 220, NULL, 'https://www.spaceforce.mil', NULL, 'United States Space Force — military space operations.', 'Arlington, Virginia', NULL),
('Roscosmos', 'RU', 'Russia', 'agency', 177, NULL, 'https://roscosmos.ru', NULL, 'Russian Federal Space Agency.', 'Moscow, Russia', NULL),
-- Launch providers
('Rocket Lab', 'US', 'United States', 'launch_provider', 5, 'Photon', 'https://www.rocketlabusa.com', 'info@rocketlabusa.com', 'Launch provider and satellite manufacturer. Electron and Neutron rockets.', 'Long Beach, California', 'NASDAQ:RKLB'),
('Arianespace', 'FR', 'France', 'launch_provider', 0, NULL, 'https://www.arianespace.com', 'info@arianespace.com', 'European launch services provider. Ariane 6, Vega-C.', 'Évry-Courcouronnes, France', NULL),
('Blue Origin', 'US', 'United States', 'launch_provider', 0, 'New Glenn', 'https://www.blueorigin.com', NULL, 'Aerospace manufacturer. New Glenn orbital launch vehicle.', 'Kent, Washington', NULL),
('Lockheed Martin', 'US', 'United States', 'manufacturer', 30, NULL, 'https://www.lockheedmartin.com', NULL, 'Major defense and aerospace company. Satellite manufacturing.', 'Bethesda, Maryland', 'NYSE:LMT'),
('Northrop Grumman', 'US', 'United States', 'manufacturer', 15, NULL, 'https://www.northropgrumman.com', NULL, 'Aerospace and defense. Cygnus spacecraft, satellite manufacturing.', 'Falls Church, Virginia', 'NYSE:NOC'),
-- SSA / Space Safety
('LeoLabs', 'US', 'United States', 'ssa_provider', 0, NULL, 'https://www.leolabs.space', 'info@leolabs.space', 'Commercial provider of LEO mapping and space situational awareness services.', 'Menlo Park, California', NULL),
('ExoAnalytic Solutions', 'US', 'United States', 'ssa_provider', 0, NULL, 'https://www.exoanalytic.com', NULL, 'Space domain awareness using ground-based telescope network.', 'El Segundo, California', NULL),
('Kayhan Space', 'US', 'United States', 'ssa_provider', 0, NULL, 'https://kayhan.space', NULL, 'Spaceflight intelligence and autonomous safety solutions. Satcat platform.', 'Denver, Colorado', NULL),
('AGI (Ansys)', 'US', 'United States', 'ssa_provider', 0, NULL, 'https://www.ansys.com/products/missions/ansys-stk', NULL, 'Systems Tool Kit (STK) for space situational awareness and mission planning.', 'Exton, Pennsylvania', 'NASDAQ:ANSS'),
('COMSPOC', 'US', 'United States', 'ssa_provider', 0, NULL, 'https://comspoc.com', NULL, 'Commercial Space Operations Center. Space safety services.', 'Chantilly, Virginia', NULL)
ON CONFLICT (name) DO UPDATE SET
    satellite_count = EXCLUDED.satellite_count,
    updated_at = NOW();

SELECT COUNT(*) AS total_entries FROM business_directory;
"""

result = subprocess.run(
    ["sudo", "-u", "postgres", "psql", "casdb"],
    input=sql, capture_output=True, text=True
)
print(result.stdout[-100:] if result.stdout else "")
if result.stderr and "ERROR" in result.stderr:
    print("STDERR:", result.stderr[:300])
print("[OK] DB + 40 seed entries")

# ═══════════════════════════════════════════════════
# 2) Auto-enrich from Space-Track country data
# ═══════════════════════════════════════════════════
print("\n[2/4] Auto-enrich satellite counts from Space-Track cache...")
if os.path.exists("/opt/cas/.satcat_owners.json"):
    with open("/opt/cas/.satcat_owners.json") as f:
        satcat = json.load(f)
    from collections import Counter
    # Count by country
    country_counts = Counter(r.get("COUNTRY","?") for r in satcat)
    # Count by name prefix (constellation detection)
    prefix_map = {
        "STARLINK": "SpaceX",
        "ONEWEB": "OneWeb",
        "KUIPER": "Amazon (Project Kuiper)",
        "IRIDIUM": "Iridium Communications",
        "GLOBALSTAR": "Globalstar",
        "ORBCOMM": "Orbcomm",
        "HULIANWANG": "Shanghai Spacecom (GeeSpace)",
        "QIANFAN": "Shanghai Spacecom (GeeSpace)",
        "GEESAT": "Shanghai Spacecom (GeeSpace)",
        "LEMUR": "Spire Global",
        "FLOCK": "Planet Labs",
        "SKYSAT": "Planet Labs",
    }
    op_counts = Counter()
    for r in satcat:
        name = r.get("SATNAME", "")
        prefix = name.split("-")[0].split(" ")[0].strip()
        if prefix in prefix_map:
            op_counts[prefix_map[prefix]] += 1

    # Update DB
    import psycopg2
    conn = psycopg2.connect(_dsn())
    cur = conn.cursor()
    for op_name, count in op_counts.items():
        cur.execute("UPDATE business_directory SET satellite_count = %s, data_source = 'space-track-auto', updated_at = NOW() WHERE name = %s", (count, op_name))
    conn.commit()
    cur.close()
    conn.close()
    print(f"[OK] Updated {len(op_counts)} operator satellite counts from Space-Track")
else:
    print("[SKIP] No Space-Track cache found")

# ═══════════════════════════════════════════════════
# 3) ENGINE ENDPOINT
# ═══════════════════════════════════════════════════
print("\n[3/4] Engine endpoint...")
s = open(ENGINE).read()

if "/api/directory" in s:
    print("[SKIP] /api/directory already exists")
else:
    marker = '        elif self.path.startswith("/api/launches"):'
    endpoint = '''        elif self.path.startswith("/api/directory"):
            # ── Business Directory ──
            try:
                import urllib.parse as _ulp
                _qs = _ulp.parse_qs(_ulp.urlparse(self.path).query)
                _cat = _qs.get("category", [None])[0]
                _country = _qs.get("country", [None])[0]
                _search = _qs.get("q", [None])[0]
                _limit = int(_qs.get("limit", [100])[0])
                conn = psycopg2.connect(os.environ.get("DB_URL",""))
                cur = conn.cursor()
                where = ["1=1"]
                params = []
                if _cat:
                    where.append("category = %s")
                    params.append(_cat)
                if _country:
                    where.append("country_code = %s")
                    params.append(_country.upper())
                if _search:
                    where.append("(LOWER(name) LIKE %s OR LOWER(description) LIKE %s OR LOWER(constellation) LIKE %s)")
                    sq = f"%{_search.lower()}%"
                    params.extend([sq, sq, sq])
                query = f"SELECT id,name,country_code,country_name,category,satellite_count,constellation,website,contact_email,description,hq_location,stock_ticker FROM business_directory WHERE {' AND '.join(where)} ORDER BY satellite_count DESC NULLS LAST LIMIT %s"
                params.append(_limit)
                cur.execute(query, params)
                cols = [d[0] for d in cur.description]
                rows = [dict(zip(cols, r)) for r in cur.fetchall()]
                # Category counts
                cur.execute("SELECT category, COUNT(*) FROM business_directory GROUP BY category ORDER BY COUNT(*) DESC")
                cats = {r[0]: r[1] for r in cur.fetchall()}
                # Country counts
                cur.execute("SELECT country_code, COUNT(*) FROM business_directory GROUP BY country_code ORDER BY COUNT(*) DESC LIMIT 20")
                countries = {r[0]: r[1] for r in cur.fetchall()}
                cur.execute("SELECT COUNT(*) FROM business_directory")
                total = cur.fetchone()[0]
                cur.close(); conn.close()
                self._json({"status":"ok","total":total,"count":len(rows),"categories":cats,"countries":countries,"entries":rows})
            except Exception as e:
                self._json({"error": str(e)}, 500)
            return

'''
    assert marker in s, "launches marker not found"
    s = s.replace(marker, endpoint + marker, 1)
    import ast; ast.parse(s)
    open(ENGINE, "w").write(s)
    print("[OK] /api/directory endpoint added")

# ═══════════════════════════════════════════════════
# 4) PORTAL: Sidebar + dispatch + render
# ═══════════════════════════════════════════════════
print("\n[4/4] Portal UI...")
p = open(PORTAL).read()

# Sidebar — add after Launches
old_launches_sidebar = """<div class="sidebar-item" onclick="showSection('launches')"><span class="icon">🚀</span> Launches</div>"""
new_with_directory = old_launches_sidebar + '\n      <div class="sidebar-item" onclick="showSection(\'directory\')"><span class="icon">🏢</span> Directory</div>'

if "showSection('directory')" not in p:
    assert old_launches_sidebar in p, "Launches sidebar not found"
    p = p.replace(old_launches_sidebar, new_with_directory, 1)
    print("[OK] Sidebar added")

# idx mapping
old_idx = "launches:14"
new_idx = "launches:14, directory:15"
if "directory:15" not in p:
    p = p.replace(old_idx, new_idx, 1)
    print("[OK] idx mapping added")

# Dispatch
old_disp = "else if (name === 'launches') renderLaunches(content);"
new_disp = old_disp + "\n  else if (name === 'directory') renderDirectory(content);"
if "renderDirectory" not in p:
    p = p.replace(old_disp, new_disp, 1)
    print("[OK] dispatch added")

# Render function — before renderAdmin
if "async function renderDirectory" not in p:
    anchor = "async function renderLaunches"
    render = r"""async function renderDirectory(el) {
  el.innerHTML = '<div class="content-header"><h2>🏢 Space Industry Directory</h2><p>Satellite operators, agencies, manufacturers, and service providers</p></div>'
    +'<div style="display:flex;gap:0;margin-bottom:16px;border-bottom:1px solid var(--border);" id="dirTabs">'
    +'<button class="dir-tab active" onclick="filterDirectory(null,this)" style="padding:10px 20px;font-family:var(--mono);font-size:10px;letter-spacing:1px;background:none;border:none;border-bottom:2px solid var(--cyan);color:var(--cyan);cursor:pointer;">ALL</button>'
    +'<button class="dir-tab" onclick="filterDirectory(\'operator\',this)" style="padding:10px 20px;font-family:var(--mono);font-size:10px;letter-spacing:1px;background:none;border:none;border-bottom:2px solid transparent;color:var(--muted);cursor:pointer;">OPERATORS</button>'
    +'<button class="dir-tab" onclick="filterDirectory(\'agency\',this)" style="padding:10px 20px;font-family:var(--mono);font-size:10px;letter-spacing:1px;background:none;border:none;border-bottom:2px solid transparent;color:var(--muted);cursor:pointer;">AGENCIES</button>'
    +'<button class="dir-tab" onclick="filterDirectory(\'manufacturer\',this)" style="padding:10px 20px;font-family:var(--mono);font-size:10px;letter-spacing:1px;background:none;border:none;border-bottom:2px solid transparent;color:var(--muted);cursor:pointer;">MANUFACTURERS</button>'
    +'<button class="dir-tab" onclick="filterDirectory(\'ssa_provider\',this)" style="padding:10px 20px;font-family:var(--mono);font-size:10px;letter-spacing:1px;background:none;border:none;border-bottom:2px solid transparent;color:var(--muted);cursor:pointer;">SSA</button>'
    +'<button class="dir-tab" onclick="filterDirectory(\'launch_provider\',this)" style="padding:10px 20px;font-family:var(--mono);font-size:10px;letter-spacing:1px;background:none;border:none;border-bottom:2px solid transparent;color:var(--muted);cursor:pointer;">LAUNCH</button>'
    +'</div>'
    +'<div style="margin-bottom:16px;"><input type="text" id="dirSearch" placeholder="Search operators, agencies, constellations..." oninput="searchDirectory()" style="width:100%;max-width:400px;padding:10px 14px;background:var(--input-bg,var(--surface));border:1px solid var(--border);border-radius:6px;color:var(--text);font-family:var(--mono);font-size:11px;"></div>'
    +'<div id="dirStats" style="margin-bottom:12px;font-family:var(--mono);font-size:10px;color:var(--muted);"></div>'
    +'<div id="dirContent">Loading...</div>';
  loadDirectory();
}

var _dirCategory = null;
var _dirSearchTimer = null;

function filterDirectory(cat, btn) {
  _dirCategory = cat;
  document.querySelectorAll('.dir-tab').forEach(function(t) { t.style.borderBottomColor='transparent'; t.style.color='var(--muted)'; });
  if (btn) { btn.style.borderBottomColor='var(--cyan)'; btn.style.color='var(--cyan)'; }
  loadDirectory();
}

function searchDirectory() {
  clearTimeout(_dirSearchTimer);
  _dirSearchTimer = setTimeout(function() { loadDirectory(); }, 300);
}

async function loadDirectory() {
  var url = API + '/api/directory?limit=100';
  if (_dirCategory) url += '&category=' + _dirCategory;
  var q = document.getElementById('dirSearch');
  if (q && q.value.length >= 2) url += '&q=' + encodeURIComponent(q.value);
  
  try {
    var r = await fetch(url).then(function(r) { return r.json(); });
    if (r.error) { document.getElementById('dirContent').innerHTML = '<p style="color:var(--red)">'+r.error+'</p>'; return; }
    
    // Stats
    var statsHtml = '<span>Total: <strong>'+r.total+'</strong> entries</span>';
    if (r.categories) {
      statsHtml += ' &nbsp;·&nbsp; ';
      var catNames = {operator:'Operators',agency:'Agencies',manufacturer:'Manufacturers',ssa_provider:'SSA',launch_provider:'Launch'};
      Object.keys(r.categories).forEach(function(k) { statsHtml += '<span style="margin-right:10px;">'+((catNames[k]||k)+': '+r.categories[k])+'</span>'; });
    }
    document.getElementById('dirStats').innerHTML = statsHtml;
    
    var entries = r.entries || [];
    if (!entries.length) { document.getElementById('dirContent').innerHTML = '<div style="text-align:center;padding:40px;color:var(--muted);font-family:var(--mono);font-size:11px;">No entries found.</div>'; return; }
    
    var catColors = {operator:'var(--cyan)',agency:'var(--orange)',manufacturer:'var(--green)',ssa_provider:'#a78bfa',launch_provider:'var(--red)'};
    var catLabels = {operator:'OPERATOR',agency:'AGENCY',manufacturer:'MANUFACTURER',ssa_provider:'SSA PROVIDER',launch_provider:'LAUNCH PROVIDER'};
    
    var html = '<div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(320px,1fr));gap:12px;">';
    entries.forEach(function(e) {
      var cc = catColors[e.category] || 'var(--muted)';
      var cl = catLabels[e.category] || e.category.toUpperCase();
      html += '<div class="api-key-box" style="border-left:3px solid '+cc+';margin:0;padding:14px 16px;">';
      // Header
      html += '<div style="display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:8px;">';
      html += '<div style="font-family:var(--mono);font-size:14px;color:var(--white);font-weight:700;">'+e.name+'</div>';
      html += '<span style="font-family:var(--mono);font-size:8px;padding:2px 8px;border:1px solid '+cc+';border-radius:3px;color:'+cc+';white-space:nowrap;">'+cl+'</span>';
      html += '</div>';
      // Details
      html += '<div style="font-family:var(--mono);font-size:10px;color:var(--muted);line-height:1.8;">';
      if (e.country_name) html += '<div>📍 '+e.country_name+(e.hq_location?' · '+e.hq_location:'')+'</div>';
      if (e.constellation) html += '<div>🛰 Constellation: <span style="color:var(--text)">'+e.constellation+'</span></div>';
      if (e.satellite_count > 0) html += '<div>📊 Satellites: <span style="color:var(--text)">'+e.satellite_count+'</span></div>';
      if (e.description) html += '<div style="color:var(--text);margin-top:4px;line-height:1.5;">'+e.description.substring(0,150)+(e.description.length>150?'...':'')+'</div>';
      html += '</div>';
      // Footer
      html += '<div style="display:flex;gap:10px;margin-top:8px;flex-wrap:wrap;">';
      if (e.website) html += '<a href="'+e.website+'" target="_blank" style="font-family:var(--mono);font-size:9px;color:var(--cyan);text-decoration:none;">Website ↗</a>';
      if (e.contact_email) html += '<a href="mailto:'+e.contact_email+'" style="font-family:var(--mono);font-size:9px;color:var(--cyan);text-decoration:none;">Contact ✉</a>';
      if (e.stock_ticker) html += '<span style="font-family:var(--mono);font-size:9px;color:var(--muted);">'+e.stock_ticker+'</span>';
      html += '</div>';
      html += '</div>';
    });
    html += '</div>';
    html += '<div style="margin-top:16px;font-family:var(--mono);font-size:9px;color:var(--muted);text-align:center;">Data: Space-Track SATCAT + manual enrichment · Showing '+entries.length+' of '+r.total+' entries</div>';
    
    document.getElementById('dirContent').innerHTML = html;
  } catch(e) {
    document.getElementById('dirContent').innerHTML = '<p style="color:var(--red)">Error: '+e.message+'</p>';
  }
}

""" + "\n"
    assert anchor in p, "renderLaunches anchor not found"
    p = p.replace(anchor, render + anchor, 1)
    print("[OK] renderDirectory + loadDirectory + filterDirectory + searchDirectory added")

open(PORTAL, "w").write(p)
print("[DONE] portal.html patched")

# ═══════════════════════════════════════════════════
# 5) RESTART + TEST
# ═══════════════════════════════════════════════════
print("\n[RESTART]")
import ast

def _dsn():
    e = {}
    with open("/opt/cas/.env") as f:
        for ln in f:
            if "=" in ln and not ln.startswith("#"):
                k, v = ln.strip().split("=", 1)
                e[k] = v.strip().strip('"').strip("'")
    return e["DB_URL"]

ast.parse(open(ENGINE).read())
print("[OK] Syntax valid")

os.system("fuser -k 8765/tcp 2>/dev/null || true")
os.system("sleep 1")
os.system("systemctl restart cas")
os.system("sleep 4")
os.system("systemctl is-active cas")

print("\n[TEST]")
os.system("curl -s http://localhost:8765/api/directory?limit=3 | python3 -c 'import json,sys;d=json.load(sys.stdin);print(\"Total:\",d.get(\"total\",0),\"| Categories:\",d.get(\"categories\",{}))'")
os.system("curl -s 'http://localhost:8765/api/directory?category=operator&limit=3' | python3 -c 'import json,sys;d=json.load(sys.stdin);[print(e[\"name\"],\"|\",e[\"satellite_count\"],\"sats\") for e in d.get(\"entries\",[])]'")

print("\n═══ DONE ═══")
print("Hard-refresh portal → 🏢 Directory tab")
