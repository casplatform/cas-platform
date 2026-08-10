#!/usr/bin/env python3
"""
TLE ARŞİV İNDİRME — LEO, 2020→bugün, HAFTALIK seyreltme
========================================================
gp_history → haftada 1 TLE/nesne → tle_history (idempotent, ON CONFLICT DO NOTHING).
Yıl-dilimli + 50'lik NORAD grupları. Kesilirse KALDIĞI YERDEN devam (progress dosyası).
cas_engine'e SIFIR dokunuş (import-only). ST'ye nazik (rate-limit beklemesi).
Kullanım:  nohup python3 tle_archive_download.py > /var/log/tle_archive.log 2>&1 &
"""
import os, sys, json, time, urllib.parse, urllib.request, http.cookiejar
from datetime import datetime, timezone

for line in open('/opt/cas/.env'):
    if '=' in line and not line.strip().startswith('#'):
        k,v=line.strip().split('=',1); os.environ.setdefault(k,v.strip().strip('"'))
sys.path.insert(0,'/opt/cas'); sys.path.insert(0,'/opt/cas/cas_api')
import cas_engine as E
import psycopg2, psycopg2.extras

ST="https://www.space-track.org"
YEARS=[2020,2021,2022,2023,2024,2025,2026]
GROUP=50                      # NORAD/grup
SLEEP_BETWEEN=12.0            # ST limit: 30/dk, 300/saat. 12s -> ~120/saat (limitin %40, sisteme pay)
MAX_PER_HOUR=120              # kendi kendini frenleme (hard cap)
PROGRESS="/opt/cas/.tle_archive_progress.json"
RE=6378.137

def log(m):
    print(f"[{datetime.now(timezone.utc).strftime('%H:%M:%S')}] {m}", flush=True)

_req_times=[]
def throttle():
    """Saatlik istek sayacı — MAX_PER_HOUR'u asla asma (ST hesap koruma)."""
    import time as _t
    now=_t.time()
    _req_times[:] = [t for t in _req_times if now-t < 3600]
    if len(_req_times) >= MAX_PER_HOUR:
        wait = 3600 - (now - _req_times[0]) + 5
        log(f"  [THROTTLE] saatlik limit ({MAX_PER_HOUR}) doldu -> {wait/60:.0f} dk bekle")
        _t.sleep(max(wait,0))
        _req_times[:] = [t for t in _req_times if _t.time()-t < 3600]
    _req_times.append(_t.time())

def load_progress():
    try: return json.load(open(PROGRESS))
    except: return {"done":[]}
def save_progress(p):
    json.dump(p, open(PROGRESS,"w"))

def st_login():
    cj=http.cookiejar.CookieJar()
    op=urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
    op.open(f"{ST}/ajaxauth/login",
            urllib.parse.urlencode({"identity":os.environ["ST_IDENTITY"],
                                    "password":os.environ["ST_PASSWORD"]}).encode(), timeout=30)
    return op

def leo_norads():
    cache=E.get_st_catalog_cache(); out=[]
    for typ,lst in (cache or {}).items():
        if isinstance(lst,list):
            for o in lst:
                if o.get("l1") and o.get("l2") and o.get("norad"):
                    try:
                        nid=str(o["norad"]).strip()
                        if not nid.isdigit():      # 'T0012' gibi geçici analist ID'leri ATLA
                            continue
                        p=E.parse_tle("X",o["l1"],o["l2"]); alt=(p["a"]/1000.0)-RE
                        if 300<alt<1400: out.append(nid)
                    except: pass
    return sorted(set(out), key=lambda x:int(x))

def week_key(epoch_str):
    """ISO yıl-hafta → haftalık seyreltme anahtarı."""
    try:
        dt=datetime.fromisoformat(epoch_str.replace("Z","+00:00"))
        y,w,_=dt.isocalendar()
        return (y,w), dt
    except Exception:
        return None, None

def main():
    t_start=time.time()
    norads=leo_norads()
    log(f"LEO nesne: {len(norads)} | yıllar: {YEARS[0]}-{YEARS[-1]} | grup={GROUP} | HAFTALIK seyreltme")
    prog=load_progress(); done=set(prog["done"])
    op=st_login(); log("Space-Track login OK")
    conn=psycopg2.connect(os.environ["DB_URL"]); conn.autocommit=False
    cur=conn.cursor()

    groups=[norads[i:i+GROUP] for i in range(0,len(norads),GROUP)]
    total_tasks=len(groups)*len(YEARS)
    task_i=0; written_total=0

    for yi,year in enumerate(YEARS):
        lo=f"{year}-01-01"; hi=f"{year}-12-31" if year<2026 else datetime.now(timezone.utc).strftime("%Y-%m-%d")
        consecutive_empty=0
        for gi,grp in enumerate(groups):
            task_i+=1
            key=f"{year}:{gi}"
            if key in done:
                continue
            # NORAD sirali: bu yilda 2 ardisik grup bossa, sonrakiler de yeni nesneler -> atla
            if consecutive_empty>=2:
                log(f"  {year}: grup {gi+1}+ ATLANIYOR (NORAD'lar bu yil henuz firlatilmamis)")
                for gj in range(gi,len(groups)):
                    done.add(f"{year}:{gj}")
                save_progress({"done":sorted(done)})
                break
            try:
                q=(f"{ST}/basicspacedata/query/class/gp_history"
                   f"/NORAD_CAT_ID/{','.join(grp)}/EPOCH/{lo}--{hi}"
                   f"/orderby/NORAD_CAT_ID%20asc/format/json")
                throttle()
                raw=op.open(q,timeout=600).read()
                recs=json.loads(raw)
            except Exception as e:
                msg=repr(e)[:150]
                wait = 120 if ("500" in msg or "429" in msg or "rate" in msg.lower()) else 30
                log(f"  HATA {key}: {msg} — {wait}s bekle, tekrar dene")
                time.sleep(wait)
                try:
                    op=st_login(); raw=op.open(q,timeout=600).read(); recs=json.loads(raw)
                except Exception as e2:
                    log(f"  ATLANDI {key}: {repr(e2)[:120]}"); continue

            # HAFTALIK SEYRELTME: her (norad, yıl-hafta) için ilk TLE
            keep={}
            for r in recs:
                l1=r.get("TLE_LINE1"); l2=r.get("TLE_LINE2")
                if not l1 or not l2: continue
                wk,dt=week_key(r.get("EPOCH",""))
                if wk is None: continue
                nid=r.get("NORAD_CAT_ID")
                k=(nid,wk)
                if k not in keep: keep[k]=(nid,dt,l1,l2,r)

            rows=[]
            for (nid,wk),(n,dt,l1,l2,r) in keep.items():
                try:
                    inc=float(r.get("INCLINATION") or 0) or None
                    mm=float(r.get("MEAN_MOTION") or 0)
                    alt=None
                    if mm>0:
                        import math
                        nrad=mm*2*math.pi/86400.0
                        a=(398600.4418/(nrad*nrad))**(1/3)
                        alt=a-RE
                    rows.append((int(n), dt, l1, l2, inc, alt))
                except Exception:
                    continue

            if rows:
                psycopg2.extras.execute_values(cur,
                    "INSERT INTO tle_history (norad,epoch,l1,l2,inc_deg,alt_km) VALUES %s "
                    "ON CONFLICT (norad,epoch) DO NOTHING", rows, page_size=1000)
                conn.commit()
                written_total+=len(rows)

            consecutive_empty = consecutive_empty+1 if len(recs)==0 else 0
            done.add(key); prog["done"]=sorted(done); save_progress(prog)
            el=time.time()-t_start
            pct=100.0*task_i/total_tasks
            eta=(el/task_i)*(total_tasks-task_i)/60 if task_i else 0
            log(f"  {year} grup {gi+1}/{len(groups)} → ham {len(recs)} → seyreltilmiş {len(rows)} "
                f"| toplam yazılan {written_total:,} | %{pct:.1f} | ETA {eta:.0f}dk")
            time.sleep(SLEEP_BETWEEN)

    cur.execute("SELECT count(*), min(epoch)::date, max(epoch)::date FROM tle_history")
    n,mn,mx=cur.fetchone()
    log(f"BİTTİ. tle_history: {n:,} satır, {mn} → {mx}. Süre {(time.time()-t_start)/3600:.1f} saat")
    cur.close(); conn.close()

if __name__=="__main__": main()
