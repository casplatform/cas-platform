#!/usr/bin/env python3
"""
refresh_demo_scenarios.py — CAS demo senaryo tazeleyici (ESA + TR).

Her calistirmada demo conjunction TCA'larini gelecege tasir, guncel TLE
geometrisiyle yeniden hesaplar. Cron: her gun 02:00.

Hesaplar:
  demo@casplatform.com (user 6) — ESA/Avrupa seti (DEMO-ESA-*, REAL-ESA-*)
  test@casplatform.com (user 2) — TR kurum/sirket seti (DEMO-TR-*, REAL-TR-*)
"""
import os, sys, math, json

_CAS_HOME = os.environ.get("CAS_HOME", "/opt/cas").rstrip("/") or "/opt/cas"
sys.path.insert(0, os.path.join(_CAS_HOME, "cas_api"))
from sgp4.api import Satrec, jday
from datetime import datetime, timezone, timedelta
import psycopg2

# DB_URL .env'den okunur (hardcoded şifre YOK)
def _load_dsn():
    env = {}
    with open(os.path.join(_CAS_HOME, ".env")) as f:
        for line in f:
            if "=" in line and not line.startswith("#"):
                k, v = line.strip().split("=", 1)
                env[k] = v.strip().strip('"').strip("'")
    dsn = env.get("DB_URL")
    if not dsn:
        raise RuntimeError("DB_URL .env'de bulunamadı")
    return dsn

DSN = _load_dsn()
ESA_USER = 6   # demo@casplatform.com
TR_USER  = 2   # test@casplatform.com

def _bessel_i0(x):
    ax = abs(x)
    if ax < 3.75:
        y = (x/3.75)**2
        return 1.0+y*(3.5156229+y*(3.0899424+y*(1.2067492+y*(0.2659732+y*(0.0360768+y*0.0045813)))))
    y = 3.75/ax
    if ax > 700: return 1e308
    return (math.exp(ax)/math.sqrt(ax))*(0.39894228+y*(0.01328592+y*(0.00225319+y*(-0.00157565+y*(0.00916281+y*(-0.02057706+y*(0.02635537+y*(-0.01647633+y*0.00392377))))))))

def collision_probability(miss_m, sigma=100.0, hbr=10.0):
    if sigma < 1e-3 or miss_m <= 0: return 0.0
    u = miss_m/sigma
    if u > 50: return 0.0
    sh = hbr/sigma; N = 200; total = 0.0
    for k in range(N):
        th = math.pi*k/N; r = u*math.cos(th)
        arg = sh*sh*0.5 - r*r*0.5
        if arg > 500: arg = 500
        try: val = math.exp(arg)*_bessel_i0(sh*u*math.cos(th))
        except OverflowError: val = 0.0
        total += val
    return min((sh*sh/(2.0*N))*total, 1.0)

def classify(pc):
    if pc >= 1e-4: return "RED"
    if pc >= 1e-5: return "YELLOW"
    return "GREEN"

def find_sigma(miss, target_pc):
    lo, hi = 10.0, 8000.0
    for _ in range(40):
        mid = (lo+hi)/2
        if collision_probability(miss, sigma=mid) > target_pc: lo = mid
        else: hi = mid
    return (lo+hi)/2

def state(sr, dt):
    jd, fr = jday(dt.year, dt.month, dt.day, dt.hour, dt.minute, dt.second+dt.microsecond*1e-6)
    e, r, v = sr.sgp4(jd, fr)
    return (r, v) if e == 0 else (None, None)

def closest_approach(sr1, sr2, start, window_h, step_s=10):
    best = (1e9, None, 0.0)
    n = int(window_h*3600/step_s)
    for i in range(n):
        t = start + timedelta(seconds=i*step_s)
        r1, v1 = state(sr1, t); r2, v2 = state(sr2, t)
        if r1 is None or r2 is None: continue
        d = math.sqrt(sum((a-b)**2 for a, b in zip(r1, r2)))*1000
        if d < best[0]:
            relv = math.sqrt(sum((a-b)**2 for a, b in zip(v1, v2)))*1000
            best = (d, t, relv)
    return best

def tle_checksum(line):
    s = 0
    for c in line[:68]:
        if c.isdigit(): s += int(c)
        elif c == '-': s += 1
    return s % 10

def get_tle(cur, user_id, name):
    cur.execute("SELECT norad_id, sat_name, tle_line1, tle_line2 FROM watchlist WHERE user_id=%s AND sat_name=%s",
                (user_id, name))
    return cur.fetchone()

def ensure_sibling(cur, user_id, primary_name, sib_norad, sib_name):
    """Primary'nin yorungesinden kucuk inclination offset'li demo objesi uretir/tazeler
    (SGP4-tutarli sahte obje; watchlist'e upsert)."""
    base = get_tle(cur, user_id, primary_name)
    if not base or not base[2] or not base[3]:
        print(f"  ! sibling: {primary_name} TLE yok (user {user_id})"); return
    _, _, bl1, bl2 = base
    incl0=float(bl2[8:16]); raan0=float(bl2[17:25]); ecc0=bl2[26:33]
    argp0=float(bl2[34:42]); ma0=float(bl2[43:51]); mm0=bl2[52:63]; rev0=bl2[63:68]
    sib_l2 = f"2 {sib_norad} {incl0+0.05:8.4f} {raan0:8.4f} {ecc0} {argp0:8.4f} {ma0:8.4f} {mm0}{rev0}"
    sib_l2 = sib_l2[:68]
    while len(sib_l2) < 68: sib_l2 += '0'
    sib_l2 += str(tle_checksum(sib_l2))
    sib_l1 = (bl1[:2]+str(sib_norad)+bl1[7:])[:68]
    sib_l1 += str(tle_checksum(sib_l1))
    cur.execute("""INSERT INTO watchlist (user_id, norad_id, sat_name, tle_line1, tle_line2)
                   VALUES (%s,%s,%s,%s,%s)
                   ON CONFLICT (user_id, norad_id)
                   DO UPDATE SET tle_line1=EXCLUDED.tle_line1, tle_line2=EXCLUDED.tle_line2""",
                (user_id, str(sib_norad), sib_name, sib_l1, sib_l2))

def main():
    conn = psycopg2.connect(DSN)
    cur = conn.cursor()
    now = datetime.now(timezone.utc)
    print(f"=== Demo senaryo tazeleme - {now.strftime('%Y-%m-%d %H:%M UTC')} ===\n")

    # Sibling demo objeleri (SGP4-tutarli)
    ensure_sibling(cur, ESA_USER, "BRITE-PL", "99431", "BRITE-PL DEMO-OBJ")
    ensure_sibling(cur, TR_USER,  "IMECE",    "99178", "IMECE DEMO-OBJ")

    # (cdm_id, user_id, n1, n2, t_miss, t_pc, synthetic, is_sibling, note_key)
    scenarios = [
        # ── ESA seti (user 6) — mevcut davranis birebir ──
        ("DEMO-ESA-RED",     ESA_USER, "BRITE-PL",     "BRITE-PL DEMO-OBJ", 150.0,  5e-4, True,  False, "red"),
        ("DEMO-ESA-YELLOW",  ESA_USER, "HYDROGNSS-1",  "PIAST-S1",          850.0,  3e-5, True,  False, "yellow"),
        ("DEMO-ESA-YELLOW2", ESA_USER, "HYDROGNSS-1",  "ICEYE-X75",         900.0,  3e-5, True,  False, "yellow"),
        ("DEMO-ESA-GREEN",   ESA_USER, "SENTINEL 3A",  "METOP SG-A",        1500.0, 3e-6, True,  False, "green"),
        ("REAL-ESA-BRITE",   ESA_USER, "BRITE-PL",     "BRITE-PL 2",        None,   None, False, False, "real"),
        ("REAL-ESA-SCIENCE", ESA_USER, "CHEOPS",       "BIOMASS",           None,   None, False, False, "real"),
        ("REAL-ESA-VLEO",    ESA_USER, "SWARM C",      "EAGLEEYE",          None,   None, False, False, "real"),
        # ── TR seti (user 2) — Turk kurum/sirket demo hesabi ──
        ("DEMO-TR-RED",      TR_USER,  "IMECE",        "IMECE DEMO-OBJ",    150.0,  5e-4, True,  False, "red"),
        ("DEMO-TR-YELLOW",   TR_USER,  "GOKTURK 2",    "TURKSAT 3U",        850.0,  3e-5, True,  False, "yellow"),
        ("DEMO-TR-YELLOW2",  TR_USER,  "GOKTURK-1A",   "RASAT",             900.0,  3e-5, True,  False, "yellow"),
        ("DEMO-TR-GREEN",    TR_USER,  "CONNECTA T1.2","FGN-100-D2",        1500.0, 3e-6, True,  False, "green"),
        ("REAL-TR-OBS",      TR_USER,  "IMECE",        "GOKTURK-1A",        None,   None, False, False, "real"),
        ("REAL-TR-HERITAGE", TR_USER,  "RASAT",        "ITUPSAT 1",         None,   None, False, False, "real"),
    ]
    NOTES = {
        "red": "ILLUSTRATIVE DEMO SCENARIO - synthetic conjunction with a demo object derived from the primary's own orbit (small inclination offset). Geometry is fully SGP4-consistent so all CAS computations resolve correctly. Demonstrates the RED / maneuver-advised decision path.",
        "yellow": "ILLUSTRATIVE DEMO SCENARIO - miss distance and covariance set to demonstrate the YELLOW / Monitor decision tier. Geometry remains TLE-consistent.",
        "green": "ILLUSTRATIVE DEMO SCENARIO - low-risk GREEN tier demonstration. Geometry remains TLE-consistent.",
        "real": "Real SGP4 close approach between two catalogued objects (no Space-Track CDM issued for this pair). Re-screened against current TLEs.",
    }

    updated = 0
    for cdm_id, uid, n1, n2, t_miss, t_pc, synthetic, is_sibling, note_key in scenarios:
        s1 = get_tle(cur, uid, n1); s2 = get_tle(cur, uid, n2)
        if not s1 or not s2:
            print(f"  ! {cdm_id}: uydu bulunamadi"); continue
        try:
            sr1 = Satrec.twoline2rv(s1[2], s1[3]); sr2 = Satrec.twoline2rv(s2[2], s2[3])
        except Exception as e:
            print(f"  ! {cdm_id}: TLE hatasi {e}"); continue
        if synthetic:
            search_start = now + timedelta(hours=24)
            miss, tca, relv = closest_approach(sr1, sr2, search_start, window_h=48, step_s=10)
        else:
            search_start = now + timedelta(hours=6)
            miss, tca, relv = closest_approach(sr1, sr2, search_start, window_h=168, step_s=15)
        if tca is None:
            print(f"  ! {cdm_id}: yakinlasma yok"); continue
        if synthetic and t_miss is not None:
            miss_final = t_miss; sigma = find_sigma(t_miss, t_pc); pc = collision_probability(t_miss, sigma=sigma)
        elif synthetic and is_sibling:
            miss_final = miss; sigma = 100.0; pc = collision_probability(miss, sigma=sigma)
        else:
            miss_final = miss; sigma = 300.0; pc = collision_probability(miss, sigma=sigma)
        risk = classify(pc)
        # raw_json must carry the same keys a real Space-Track CDM does. The
        # engine's watchlist scan filters on raw_json->>'norad1'/'norad2' and
        # reads sat1/sat2/Pc/Pc_str/risk/miss_distance_m/tca_str/cdm_id from the
        # same blob, while decision_scanner reads the table columns. Writing
        # only columns (as this script did until 2026-08-16) meant the two
        # layers disagreed: Decisions raised "Maneuver advised" on the demo RED
        # scenarios while My Satellites reported 0 RED for the same accounts --
        # on the very accounts operators are shown. The synthetic/demo/note keys
        # stay: a demo scenario should be labelled as one, not disguised.
        raw = {"synthetic": synthetic, "demo": synthetic, "rel_velocity_ms": round(relv, 1),
               "sigma_assumed_m": round(sigma, 1),
               "source": "CAS demo scenario" if synthetic else "CAS real-geometry screening",
               "note": NOTES[note_key], "refreshed_at": now.isoformat(),
               "cdm_id": cdm_id,
               "sat1": s1[1], "sat2": s2[1],
               "norad1": str(s1[0]), "norad2": str(s2[0]),
               "miss_distance_m": round(float(miss_final), 1),
               "miss_distance_km": round(float(miss_final) / 1000.0, 4),
               "Pc": float(pc), "Pc_str": f"{pc:.3e}",
               "risk": risk,
               "tca_str": tca.isoformat(),
               "tca_hours": round((tca - now).total_seconds() / 3600.0, 2),
               "relative_velocity_ms": round(relv, 1)}
        if is_sibling: raw["demo_object"] = True
        cur.execute("DELETE FROM conjunction_events WHERE cdm_id=%s", (cdm_id,))
        cur.execute("""INSERT INTO conjunction_events (cdm_id,sat1,sat2,norad1,norad2,tca,miss_dist_m,pc,risk,raw_json,fetched_at)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,NOW())""",
                    (cdm_id, s1[1], s2[1], s1[0], s2[0], tca, miss_final, pc, risk, json.dumps(raw)))
        tag = "DEMO" if synthetic else "REAL"
        hrs = (tca - now).total_seconds()/3600
        print(f"  [{tag}] {cdm_id:<18} u{uid} {n1[:11]}x{n2[:11]:11} {miss_final:>6.0f}m Pc={pc:.1e} {risk:6} TCA +{hrs:.0f}h")
        updated += 1

    conn.commit()
    print(f"\n+ {updated} senaryo tazelendi")
    conn.close()
    print("\n=== Decision scanner ===")
    import subprocess
    result = subprocess.run(["python3", os.path.join(_CAS_HOME, "decision_scanner.py")],
                            capture_output=True, text=True, cwd=_CAS_HOME)
    for line in result.stdout.splitlines():
        if "User 6" in line or "User 2" in line or "Total" in line:
            print(f"  {line.strip()}")
    print("\n+ Tazeleme tamamlandi. Demo (ESA) + Test (TR) hesaplari hazir.")

if __name__ == "__main__":
    main()
