#!/usr/bin/env python3
"""
BACKFILL PROTOTİP — DRY-RUN (tek nesne × son 30 gün)
=====================================================
GP_History geçmiş TLE → SGP4 ile o tarihe propagate → screen_conjunctions →
yakın-geçişler. *** DB'YE YAZMAZ *** — sadece ne üreteceğini raporlar.
cas_engine.py'ye SIFIR dokunuş (import-only). Read-only.
"""
import os, sys, math, json, time, urllib.parse, urllib.request, http.cookiejar
from datetime import datetime, timedelta, timezone

_CAS_HOME = os.environ.get("CAS_HOME", "/opt/cas").rstrip("/") or "/opt/cas"
for line in open(os.path.join(_CAS_HOME, ".env")):
    if '=' in line and not line.strip().startswith('#'):
        k,v=line.strip().split('=',1); os.environ.setdefault(k,v.strip().strip('"'))
sys.path.insert(0, _CAS_HOME); sys.path.insert(0, os.path.join(_CAS_HOME, "cas_api"))

import cas_engine as E          # mevcut motor — SADECE okuruz
from sgp4.api import Satrec, jday

ST_BASE="https://www.space-track.org"
DENEME_NORAD = sys.argv[1] if len(sys.argv)>1 else "39030"  # default GOKTURK-2 (SSO ~700km)
DAYS_BACK = 30
STEP_DAYS = 5
BAND_KM = 60
SCREEN_HOURS = 24

def st_login():
    cj=http.cookiejar.CookieJar(); op=urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
    op.open(f"{ST_BASE}/ajaxauth/login",
            urllib.parse.urlencode({"identity":os.environ["ST_IDENTITY"],
                                    "password":os.environ["ST_PASSWORD"]}).encode(), timeout=30)
    return op

def gp_history_near(op, norad, when_dt):
    """norad için when_dt'ye en yakın (ve öncesindeki) TLE'yi getir (boşluk=%20 encode)."""
    # EPOCH <= when, en yeni; boşluk YASAK → %20
    lo=(when_dt - timedelta(days=10)).strftime("%Y-%m-%d")
    hi=when_dt.strftime("%Y-%m-%d")
    q=(f"{ST_BASE}/basicspacedata/query/class/gp_history/NORAD_CAT_ID/{norad}"
       f"/EPOCH/{lo}--{hi}/orderby/EPOCH%20desc/limit/1/format/json")
    d=json.loads(op.open(q,timeout=60).read())
    return d[0] if d else None

def omm_to_tle_lines(rec):
    """gp_history kaydı TLE_LINE1/2 taşır — direkt kullan."""
    return rec.get("TLE_LINE1"), rec.get("TLE_LINE2")

def sgp4_state(l1, l2, when_dt):
    """TLE'yi when_dt anına propagate → ECI pos(m), vel(m/s). (SGP4 TEME km/s)."""
    sat=Satrec.twoline2rv(l1, l2)
    jd, fr = jday(when_dt.year, when_dt.month, when_dt.day,
                  when_dt.hour, when_dt.minute, when_dt.second + when_dt.microsecond*1e-6)
    err, r, v = sat.sgp4(jd, fr)
    if err!=0: return None, None
    return [x*1000.0 for x in r], [x*1000.0 for x in v]  # km→m

def main():
    print("="*72)
    print(f"BACKFILL DRY-RUN (YAZMA YOK) — NORAD {DENEME_NORAD}, son {DAYS_BACK} gün, {STEP_DAYS}g adım")
    print("="*72)
    op=st_login(); print("Space-Track login OK")

    # Komşu katalog: MEVCUT cache'ten hedefin bandındaki nesneler (canlı fetch_catalog_tles mantığı)
    # Hedefin şu anki irtifasını bul (cache'ten), sonra o banttaki nesneleri al.
    cache=E.get_st_catalog_cache()
    if not cache: print("KATALOG CACHE YOK — dur."); return
    # cache formatı: {"debris":[{norad,l1,l2}], "payload":[...], ...}
    all_objs=[]
    for typ, lst in cache.items():
        if isinstance(lst, list):
            for o in lst:
                if o.get("l1") and o.get("l2"):
                    all_objs.append(o)
    print(f"Cache: {len(all_objs)} TLE'li nesne")

    # hedefin irtifası (mevcut TLE'sinden)
    tgt=next((o for o in all_objs if str(o.get("norad"))==str(DENEME_NORAD)), None)
    if not tgt:
        print(f"NORAD {DENEME_NORAD} cache'te bulunamadı (l1/l2 ile). Farklı NORAD dene."); return
    tgt_alt=E._alt_from_l2(tgt["l2"]) if hasattr(E,"_alt_from_l2") else None
    # _alt_from_l2 yoksa parse_tle ile
    if tgt_alt is None:
        p=E.parse_tle("T", tgt["l1"], tgt["l2"]); tgt_alt=(p["a"]/1000.0)-6378.137
    print(f"Hedef {DENEME_NORAD} irtifa ≈ {tgt_alt:.0f} km")

    # komşu band (±BAND_KM) — screen_conjunctions için orbital dict listesi
    band=[]
    for o in all_objs:
        if str(o.get("norad"))==str(DENEME_NORAD): continue
        try:
            p=E.parse_tle(o.get("name","OBJ"), o["l1"], o["l2"])
            alt=(p["a"]/1000.0)-6378.137
            if abs(alt-tgt_alt)<=BAND_KM:
                p["altitude_km"]=alt
                band.append(p)
        except Exception:
            continue
    print(f"Komşu band (±{BAND_KM}km): {len(band)} nesne\n")

    # geçmiş kesitler
    now=datetime.now(timezone.utc)
    total_would_write=0
    print(f"{'Tarih (T)':<12}{'hedef TLE epoch':<22}{'yakin-gecis':>12}{'en yakin (m)':>14}")
    print("-"*60)
    for d in range(0, DAYS_BACK+1, STEP_DAYS):
        T=now - timedelta(days=d)
        rec=gp_history_near(op, DENEME_NORAD, T)
        time.sleep(1.5)  # ST rate-limit nezaketi
        if not rec:
            print(f"{T.strftime('%Y-%m-%d'):<12}{'(TLE yok)':<22}{'-':>12}{'-':>14}"); continue
        l1,l2=omm_to_tle_lines(rec)
        if not l1 or not l2:
            print(f"{T.strftime('%Y-%m-%d'):<12}{'(TLE_LINE yok)':<22}{'-':>12}{'-':>14}"); continue
        pos1,vel1=sgp4_state(l1,l2,T)
        if pos1 is None:
            print(f"{T.strftime('%Y-%m-%d'):<12}{'(SGP4 hata)':<22}{'-':>12}{'-':>14}"); continue
        # NOT: komşu band nesnelerini de gerçekte T'ye propagate etmek gerekir;
        # DRY-RUN'da mevcut elementlerini kullanıyoruz (yaklaşık) — ölçekte GP_History'den T-TLE.
        res=E.screen_conjunctions(pos1, vel1, band, hours=SCREEN_HOURS, threshold_km=50)
        n=len(res)
        closest=f"{res[0]['miss_distance_m']:.0f}" if res else "-"
        total_would_write+=n
        ep=rec.get("EPOCH","?")[:19]
        print(f"{T.strftime('%Y-%m-%d'):<12}{ep:<22}{n:>12}{closest:>14}")

    print("-"*60)
    print(f"\nTOPLAM üretilecek kayıt (DRY-RUN, YAZILMADI): {total_would_write}")
    print("Örnek cdm_id formatı: BACKFILL-<norad1>-<norad2>-<tca_iso>")
    print("fetched_at = geçmiş tarih T | UNIQUE(cdm_id,fetched_at) → idempotent")
    print("\n*** DB'YE HİÇBİR ŞEY YAZILMADI. Mantık doğruysa gerçek yazıma geçeriz. ***")

if __name__=="__main__": main()
