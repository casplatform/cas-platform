#!/usr/bin/env python3
"""
CAS — Launch Schedule Feature Deployment
Engine cache endpoint + Portal sidebar + render function
"""
import os, re

PORTAL = "/opt/cas/static/portal.html"
ENGINE = "/opt/cas/cas_engine.py"
TS = __import__("datetime").datetime.now().strftime("%Y%m%d_%H%M%S")
BACKUP = "/root/nginx_backups"

os.makedirs(BACKUP, exist_ok=True)
os.system(f"cp {PORTAL} {BACKUP}/portal.html.bak.launches.{TS}")
os.system(f"cp {ENGINE} {BACKUP}/cas_engine.py.bak.launches.{TS}")

print("═══ LAUNCH SCHEDULE DEPLOYMENT ═══\n")

# ════════════════════════════════════════════════════════════
# 1) ENGINE: Add /api/launches endpoint with 6h cache
# ════════════════════════════════════════════════════════════
print("[1/4] Engine endpoint...")
s = open(ENGINE).read()

if "/api/launches" in s:
    print("[SKIP] /api/launches already exists")
else:
    # Insert before the /history handler
    marker = '        elif self.path.startswith("/history"):'
    
    endpoint_code = '''        elif self.path.startswith("/api/launches"):
            # ── Upcoming Launch Schedule (TheSpaceDevs API, 6h cache) ──
            try:
                import urllib.request, time as _time
                cache_file = "/opt/cas/.launches_cache.json"
                cache_ttl = 6 * 3600  # 6 hours
                use_cache = False
                if os.path.exists(cache_file):
                    age = _time.time() - os.path.getmtime(cache_file)
                    if age < cache_ttl:
                        use_cache = True
                if use_cache:
                    with open(cache_file) as _f:
                        cached = json.load(_f)
                    self._json(cached)
                else:
                    url = "https://ll.thespacedevs.com/2.2.0/launch/upcoming/?limit=20&format=json"
                    req = urllib.request.Request(url, headers={"User-Agent": "CAS/1.0"})
                    ctx = __import__("ssl").create_default_context()
                    with urllib.request.urlopen(req, timeout=15, context=ctx) as resp:
                        raw = json.loads(resp.read().decode("utf-8"))
                    launches = []
                    for r in raw.get("results", []):
                        mission = r.get("mission") or {}
                        pad = r.get("pad") or {}
                        lsp = r.get("launch_service_provider") or {}
                        status = r.get("status") or {}
                        rocket = r.get("rocket") or {}
                        rocket_cfg = rocket.get("configuration") or {}
                        launches.append({
                            "id": r.get("id", ""),
                            "name": r.get("name", ""),
                            "net": r.get("net", ""),
                            "window_start": r.get("window_start", ""),
                            "window_end": r.get("window_end", ""),
                            "status": status.get("abbrev", ""),
                            "status_name": status.get("name", ""),
                            "provider": lsp.get("name", ""),
                            "provider_type": lsp.get("type", ""),
                            "rocket": rocket_cfg.get("name", r.get("name", "").split("|")[0].strip()),
                            "mission_name": mission.get("name", ""),
                            "mission_type": mission.get("type", ""),
                            "mission_desc": (mission.get("description") or "")[:300],
                            "pad_name": pad.get("name", ""),
                            "location": (pad.get("location") or {}).get("name", ""),
                            "country_code": (pad.get("location") or {}).get("country_code", ""),
                            "image": r.get("image", ""),
                            "webcast_live": r.get("webcast_live", False),
                        })
                    result = {
                        "status": "ok",
                        "count": len(launches),
                        "cached": False,
                        "launches": launches,
                        "source": "TheSpaceDevs Launch Library 2",
                    }
                    # Save cache
                    try:
                        with open(cache_file, "w") as _f:
                            json.dump(result, _f)
                        result["cached"] = False
                    except Exception:
                        pass
                    self._json(result)
            except Exception as e:
                self._json({"error": f"Launch data fetch failed: {e}", "launches": []}, 500)
            return

'''
    assert marker in s, f"Marker not found: {marker}"
    s = s.replace(marker, endpoint_code + marker, 1)
    
    import ast
    ast.parse(s)
    open(ENGINE, "w").write(s)
    print("[OK] /api/launches endpoint added (6h cache)")

# ════════════════════════════════════════════════════════════
# 2) PORTAL: Add sidebar item
# ════════════════════════════════════════════════════════════
print("\n[2/4] Sidebar...")
p = open(PORTAL).read()

# Add Launches after Trends, before Admin
old_sidebar = '      <div class="sidebar-item admin-only" onclick="showSection(\'admin\')" style="display:none;"><span class="icon">🛡</span> Admin</div>'
new_sidebar = '      <div class="sidebar-item" onclick="showSection(\'launches\')"><span class="icon">🚀</span> Launches</div>\n' + '      ' + old_sidebar

if "showSection('launches')" not in p:
    assert old_sidebar in p, "Admin sidebar not found"
    p = p.replace(old_sidebar, new_sidebar, 1)
    print("[OK] Sidebar item added")
else:
    print("[SKIP] Already exists")

# ════════════════════════════════════════════════════════════
# 3) PORTAL: Add dispatch + idx mapping
# ════════════════════════════════════════════════════════════
print("\n[3/4] Dispatch + idx...")

# Update idx mapping
old_idx = "top_debris:13"
new_idx = "top_debris:13, launches:14"
if "launches:14" not in p:
    p = p.replace(old_idx, new_idx, 1)
    print("[OK] idx mapping: launches:14")

# Add dispatch
old_dispatch = "  else if (name === 'top_debris') renderTopDebris(content);"
new_dispatch = old_dispatch + "\n  else if (name === 'launches') renderLaunches(content);"
if "renderLaunches" not in p:
    p = p.replace(old_dispatch, new_dispatch, 1)
    print("[OK] dispatch added")

# ════════════════════════════════════════════════════════════
# 4) PORTAL: Add renderLaunches function
# ════════════════════════════════════════════════════════════
print("\n[4/4] Render function...")

if "async function renderLaunches" not in p:
    # Insert before renderAdmin
    anchor = "async function renderAdmin(el)"
    
    render_func = r"""async function renderLaunches(el) {
  el.innerHTML = '<div class="content-header"><h2>🚀 Upcoming Launches</h2><p>Real-time launch schedule from TheSpaceDevs</p></div><div id="launchContent">Loading...</div>';
  try {
    const r = await fetch(API+'/api/launches').then(r=>r.json());
    if (r.error) { document.getElementById('launchContent').innerHTML = '<p style="color:var(--red)">'+r.error+'</p>'; return; }
    const launches = r.launches || [];
    if (!launches.length) { document.getElementById('launchContent').innerHTML = '<p style="color:var(--muted)">No upcoming launches found.</p>'; return; }
    
    const now = new Date();
    let html = '<div style="display:flex;flex-direction:column;gap:10px;">';
    
    launches.forEach(function(l) {
      const net = new Date(l.net);
      const diff = net - now;
      const isPast = diff < 0;
      const days = Math.floor(Math.abs(diff) / 86400000);
      const hours = Math.floor((Math.abs(diff) % 86400000) / 3600000);
      const mins = Math.floor((Math.abs(diff) % 3600000) / 60000);
      
      let countdown = '';
      if (isPast) {
        countdown = '<span style="color:var(--green);">Launched</span>';
      } else if (days > 0) {
        countdown = 'T-' + days + 'd ' + hours + 'h';
      } else {
        countdown = 'T-' + hours + 'h ' + mins + 'm';
      }
      
      // Status color
      var statusColor = 'var(--muted)';
      if (l.status === 'Go') statusColor = 'var(--green)';
      else if (l.status === 'TBD' || l.status === 'Hold') statusColor = 'var(--orange)';
      else if (l.status === 'Success') statusColor = 'var(--cyan)';
      else if (l.status === 'Failure') statusColor = 'var(--red)';
      else if (l.status === 'In Flight') statusColor = '#a78bfa';
      
      // Urgency border
      var borderColor = 'var(--border)';
      if (!isPast && diff < 24*3600000) borderColor = 'var(--red)';
      else if (!isPast && diff < 72*3600000) borderColor = 'var(--orange)';
      
      html += '<div class="api-key-box" style="border-left:3px solid '+borderColor+';margin-bottom:0;padding:14px 16px;">';
      html += '<div style="display:flex;justify-content:space-between;align-items:flex-start;gap:12px;flex-wrap:wrap;">';
      
      // Left: mission info
      html += '<div style="flex:1;min-width:200px;">';
      html += '<div style="font-family:var(--mono);font-size:14px;color:var(--white);font-weight:700;margin-bottom:4px;">'+l.name+'</div>';
      html += '<div style="display:flex;gap:12px;flex-wrap:wrap;font-family:var(--mono);font-size:10px;color:var(--muted);margin-bottom:6px;">';
      html += '<span>'+l.provider+'</span>';
      if (l.mission_type) html += '<span style="color:var(--text);">'+l.mission_type+'</span>';
      html += '</div>';
      if (l.mission_desc) {
        html += '<div style="font-family:var(--mono);font-size:10px;color:var(--text);line-height:1.5;margin-bottom:6px;">'+l.mission_desc.substring(0,200)+(l.mission_desc.length>200?'...':'')+'</div>';
      }
      html += '<div style="font-family:var(--mono);font-size:10px;color:var(--muted);">';
      html += '<span>📍 '+(l.location || l.pad_name || 'Unknown')+'</span>';
      html += '</div>';
      html += '</div>';
      
      // Right: countdown + status
      html += '<div style="text-align:right;min-width:120px;">';
      html += '<div style="font-family:var(--mono);font-size:18px;font-weight:700;color:var(--white);margin-bottom:4px;">'+countdown+'</div>';
      html += '<div style="font-family:var(--mono);font-size:10px;padding:3px 10px;border:1px solid '+statusColor+';border-radius:3px;color:'+statusColor+';display:inline-block;">'+l.status+'</div>';
      html += '<div style="font-family:var(--mono);font-size:9px;color:var(--muted);margin-top:6px;">'+l.net.replace('T',' ').substring(0,16)+' UTC</div>';
      html += '</div>';
      
      html += '</div></div>';
    });
    
    html += '</div>';
    html += '<div style="margin-top:16px;font-family:var(--mono);font-size:9px;color:var(--muted);text-align:center;">Data: TheSpaceDevs Launch Library 2 · Updated every 6 hours</div>';
    
    document.getElementById('launchContent').innerHTML = html;
  } catch(e) {
    document.getElementById('launchContent').innerHTML = '<p style="color:var(--red)">Error: '+e.message+'</p>';
  }
}

"""
    assert anchor in p, "renderAdmin anchor not found"
    p = p.replace(anchor, render_func + anchor, 1)
    print("[OK] renderLaunches function added")
else:
    print("[SKIP] Already exists")

open(PORTAL, "w").write(p)
print("\n[DONE] portal.html patched")

# ════════════════════════════════════════════════════════════
# 5) NGINX: /api/launches location (if needed)
# ════════════════════════════════════════════════════════════
# /api/ prefix is already proxied, no nginx change needed

# ════════════════════════════════════════════════════════════
# 6) Syntax check + restart
# ════════════════════════════════════════════════════════════
print("\n[RESTART] Checking syntax + restarting...")
import ast
ast.parse(open(ENGINE).read())
print("[OK] Engine syntax valid")

os.system("fuser -k 8765/tcp 2>/dev/null || true")
os.system("sleep 1")
os.system("systemctl restart cas")
os.system("sleep 4")
os.system("systemctl is-active cas")

# Test
print("\n[TEST] /api/launches...")
os.system('curl -s http://localhost:8765/api/launches | python3 -c \'import json,sys;d=json.load(sys.stdin);print("Launches:",d.get("count",0),"| Status:",d.get("status","error"))\'')

print("\n═══ DONE ═══")
print("Hard-refresh portal → Launches tab in sidebar")
