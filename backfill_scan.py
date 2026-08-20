#!/usr/bin/env python3
"""
BACKFILL T-SENKRON TARAMA — tle_history arşivinden geçmiş conjunction üretimi
=============================================================================
Bir geçmiş tarih T için:
  1) tle_history'den ilgili nesnelerin T'ye en yakın TLE'lerini al (LOKAL, ST sorgusu YOK)
  2) HEPSİNİ SGP4 ile aynı T anına propagate et  → zaman-senkron ECI state
  3) state → orbital element (Kepler) → screen_conjunctions'a ver (motor mantığı aynen)
  4) sonuçları conjunction_events'e BACKFILL- önekiyle idempotent yaz
cas_engine'e SIFIR dokunuş (import-only). Pc/risk motorun kendi fonksiyonlarıyla.
Kullanım:
  python3 backfill_scan.py --date 2025-06-15 --norad 39030 --dry-run
  python3 backfill_scan.py --date 2025-06-15 --norad 39030 --write
"""
import os, sys, math, argparse
from datetime import datetime, timezone, timedelta

_CAS_HOME = os.environ.get("CAS_HOME", "/opt/cas").rstrip("/") or "/opt/cas"
for line in open(os.path.join(_CAS_HOME, ".env")):
    if '=' in line and not line.strip().startswith('#'):
        k,v=line.strip().split('=',1); os.environ.setdefault(k,v.strip().strip('"'))
sys.path.insert(0, _CAS_HOME); sys.path.insert(0, os.path.join(_CAS_HOME, "cas_api"))

import cas_engine as E                      # motor: SADECE okuruz
from sgp4.api import Satrec, jday
import psycopg2, psycopg2.extras

MU = 398600.4418e9      # m^3/s^2  (motor SI kullanıyor)
RE_KM = 6378.137
BAND_KM = 60            # hedef irtifa ± bant
SCREEN_HOURS = 24
THRESHOLD_KM = 50

def db():
    return psycopg2.connect(os.environ["DB_URL"])

def sgp4_state_at(l1, l2, when):
    """TLE → when anındaki ECI (TEME) pos/vel, SI (m, m/s)."""
    sat = Satrec.twoline2rv(l1, l2)
    jd, fr = jday(when.year, when.month, when.day, when.hour, when.minute,
                  when.second + when.microsecond*1e-6)
    err, r, v = sat.sgp4(jd, fr)
    if err != 0:
        return None, None
    return [x*1000.0 for x in r], [x*1000.0 for x in v]

def state_to_elements(pos, vel):
    """ECI state (SI) → {a,e,i,raan,aop,nu} (SI + radyan) — orbital_to_eci'nin tersi.
    Standart Kepler dönüşümü (h ve e vektörleri)."""
    x,y,z = pos; vx,vy,vz = vel
    r = math.sqrt(x*x+y*y+z*z)
    v2 = vx*vx+vy*vy+vz*vz
    # angular momentum h = r x v
    hx = y*vz - z*vy; hy = z*vx - x*vz; hz = x*vy - y*vx
    h = math.sqrt(hx*hx+hy*hy+hz*hz)
    if h == 0 or r == 0: return None
    # inclination
    i = math.acos(max(-1.0, min(1.0, hz/h)))
    # node vector n = k x h
    nx, ny = -hy, hx
    n = math.sqrt(nx*nx+ny*ny)
    # eccentricity vector
    rv = x*vx + y*vy + z*vz
    ex = (v2 - MU/r)*x/MU - rv*vx/MU
    ey = (v2 - MU/r)*y/MU - rv*vy/MU
    ez = (v2 - MU/r)*z/MU - rv*vz/MU
    e = math.sqrt(ex*ex+ey*ey+ez*ez)
    # semi-major axis
    energy = v2/2.0 - MU/r
    if abs(energy) < 1e-12: return None
    a = -MU/(2.0*energy)
    if a <= 0: return None
    # RAAN
    if n > 1e-10:
        raan = math.acos(max(-1.0,min(1.0, nx/n)))
        if ny < 0: raan = 2*math.pi - raan
    else:
        raan = 0.0
    # argument of perigee
    if n > 1e-10 and e > 1e-10:
        aop = math.acos(max(-1.0,min(1.0,(nx*ex+ny*ey)/(n*e))))
        if ez < 0: aop = 2*math.pi - aop
    else:
        aop = 0.0
    # true anomaly
    if e > 1e-10:
        nu = math.acos(max(-1.0,min(1.0,(ex*x+ey*y+ez*z)/(e*r))))
        if rv < 0: nu = 2*math.pi - nu
    else:
        nu = math.acos(max(-1.0,min(1.0,(nx*x+ny*y)/(n*r)))) if n>1e-10 else 0.0
        if z < 0: nu = 2*math.pi - nu
    return {"a":a, "e":e, "i":i, "raan":raan, "aop":aop, "nu":nu}

def tle_at(cur, norad, when, window_days=10):
    """tle_history'den norad için when'e en yakın (öncesindeki) TLE — LOKAL."""
    cur.execute("""SELECT l1,l2,epoch FROM tle_history
                   WHERE norad=%s AND epoch <= %s AND epoch >= %s
                   ORDER BY epoch DESC LIMIT 1""",
                (int(norad), when, when - timedelta(days=window_days)))
    return cur.fetchone()

def band_norads(cur, when, alt_lo, alt_hi, window_days=10, limit=4000):
    """T civarında verilen irtifa bandındaki nesneler (arşivden)."""
    cur.execute("""SELECT DISTINCT ON (norad) norad, l1, l2, epoch, alt_km
                   FROM tle_history
                   WHERE epoch <= %s AND epoch >= %s
                     AND alt_km BETWEEN %s AND %s
                   ORDER BY norad, epoch DESC
                   LIMIT %s""",
                (when, when - timedelta(days=window_days), alt_lo, alt_hi, limit))
    return cur.fetchall()

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", required=True, help="geçmiş tarih YYYY-MM-DD")
    ap.add_argument("--norad", required=True, help="hedef NORAD")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--write", action="store_true")
    args = ap.parse_args()
    if not (args.dry_run or args.write):
        args.dry_run = True

    T = datetime.strptime(args.date, "%Y-%m-%d").replace(tzinfo=timezone.utc, hour=12)
    conn = db(); cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    # 1) hedefin T'deki TLE'si (arşivden)
    tgt = tle_at(cur, args.norad, T)
    if not tgt:
        print(f"HATA: NORAD {args.norad} için {args.date} civarında arşivde TLE yok.")
        print("      (arşiv indirmesi devam ediyor olabilir; o NORAD henüz gelmemiş olabilir)")
        return
    pos1, vel1 = sgp4_state_at(tgt["l1"], tgt["l2"], T)
    if pos1 is None:
        print("HATA: hedef SGP4 propagasyonu başarısız."); return
    tgt_alt = (math.sqrt(sum(p*p for p in pos1))/1000.0) - RE_KM
    print(f"Hedef {args.norad} @ {args.date}: irtifa {tgt_alt:.0f} km (TLE epoch {tgt['epoch']})")

    # 2) bant nesneleri (arşivden) → HEPSİ T'ye senkronize
    rows = band_norads(cur, T, tgt_alt-BAND_KM, tgt_alt+BAND_KM)
    print(f"Bant ±{BAND_KM}km: {len(rows)} nesne (arşivden, T'ye senkronize ediliyor)")
    catalog = []
    for r in rows:
        if str(r["norad"]) == str(args.norad): continue
        p, v = sgp4_state_at(r["l1"], r["l2"], T)
        if p is None: continue
        el = state_to_elements(p, v)
        if not el: continue
        el["name"] = f"NORAD-{r['norad']}"
        el["norad"] = str(r["norad"])
        el["altitude_km"] = r["alt_km"] or 0.0
        catalog.append(el)
    print(f"T'ye senkronize edilen katalog: {len(catalog)} nesne\n")

    # 3) motorun kendi tarama mantığı (coarse+fine+Pc+risk) — AYNEN
    res = E.screen_conjunctions(pos1, vel1, catalog,
                                hours=SCREEN_HOURS, threshold_km=THRESHOLD_KM)
    print(f"BULUNAN YAKIN-GEÇİŞ: {len(res)}")
    for r in res[:10]:
        print(f"  {r['sat_name']:<16} miss={r['miss_distance_m']:>9.0f}m  "
              f"tca=+{r['tca_hours']:.1f}h  Pc={r['Pc_str']}  {r['risk']}")
    if len(res) > 10: print(f"  ... +{len(res)-10} tane daha")

    # 4) yazma (sadece --write ile)
    if args.write and res:
        ins = []
        for r in res:
            tca = T + timedelta(hours=float(r["tca_hours"]))
            cdm_id = f"BACKFILL-{args.norad}-{r['norad']}-{tca.strftime('%Y%m%dT%H%M%S')}"
            ins.append((cdm_id, T, f"NORAD-{args.norad}", r["sat_name"],
                        str(args.norad), str(r["norad"]), tca,
                        float(r["miss_distance_m"]), float(r["Pc"]), r["risk"],
                        psycopg2.extras.Json({"source":"CAS backfill T-sync",
                                              "backfill_date":args.date,
                                              "synthetic": False})))
        w = conn.cursor()
        psycopg2.extras.execute_values(w,
            "INSERT INTO conjunction_events "
            "(cdm_id,fetched_at,sat1,sat2,norad1,norad2,tca,miss_dist_m,pc,risk,raw_json) "
            "VALUES %s ON CONFLICT (cdm_id,fetched_at) DO NOTHING", ins, page_size=500)
        conn.commit()
        print(f"\nYAZILDI: {len(ins)} kayıt (cdm_id BACKFILL- önekli, idempotent)")
    elif args.write:
        print("\nYazılacak kayıt yok.")
    else:
        print(f"\n*** DRY-RUN: DB'ye yazılmadı. --write ile yazılır. ***")

    cur.close(); conn.close()

if __name__ == "__main__":
    main()
