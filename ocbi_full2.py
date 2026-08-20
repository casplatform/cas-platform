#!/usr/bin/env python3
"""OCBI TAM İSKELET v2 — S terimi veri gerçeğine göre (medyan 52m: her şey yakın,
ayrım SIKI eşiklerde ve pc'de). Tip fix (Decimal→float)."""
import os, sys, math
_CAS_HOME = os.environ.get("CAS_HOME", "/opt/cas").rstrip("/") or "/opt/cas"
for line in open(os.path.join(_CAS_HOME, ".env")):
    if '=' in line and not line.strip().startswith('#'):
        k,v=line.strip().split('=',1); os.environ.setdefault(k,v.strip().strip('"'))
sys.path.insert(0, _CAS_HOME); sys.path.insert(0, os.path.join(_CAS_HOME, "cas_api"))
from cas_api.services import mission_design as md
import psycopg2, psycopg2.extras
from collections import Counter

MU=398600.4418; RE=6378.137; A_REF_KM2=1e-5; SEC_PER_YEAR=3.15576e7
HALF=25.0; INC_TOL=5.0; ALPHA=1.0; BETA=0.5; GAMMA=0.5
def shell_volume(c,h): return (4/3)*math.pi*((RE+c+h)**3-(RE+c-h)**3)
def v_rel(c): vc=math.sqrt(MU/(RE+c)); return (4/math.pi)*vc
def db(): return psycopg2.connect(os.environ["DB_URL"])

def load_conj(norad_alt):
    """Tüm conjunction'ları irtifa+miss+pc ile belleğe al (tek sorgu, sonra bant-filtre)."""
    conn=db(); cur=conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("SELECT norad1,norad2,miss_dist_m,pc FROM conjunction_events")
    out=[]
    for r in cur.fetchall():
        alts=[norad_alt.get(str(r["norad1"]).strip()), norad_alt.get(str(r["norad2"]).strip())]
        alts=[a for a in alts if a is not None]
        if not alts: continue
        out.append((sum(alts)/len(alts), float(r["miss_dist_m"]) if r["miss_dist_m"] is not None else None,
                    float(r["pc"]) if r["pc"] is not None else None))
    cur.close(); conn.close()
    return out

def band_slice(conj, center):
    lo,hi=center-HALF,center+HALF
    return [(m,p) for (a,m,p) in conj if lo<=a<=hi]

def severity_strict(rows):
    """S_a = sıkı miss eşikleri {200,100,50,25}m ağırlıklı CDF (ayrım burada)."""
    ms=[m for m,p in rows if m is not None]
    if not ms: return 0.0,None
    n=len(ms); thr={200:0,100:0,50:0,25:0}
    for m in ms:
        for d in thr:
            if m<d: thr[d]+=1
    w={200:1,100:2,50:4,25:8}
    S=sum(w[d]*(thr[d]/n) for d in thr)/sum(w.values())
    return S, sorted(ms)[n//2]  # S, medyan

def severity_pc(rows):
    """S_b = pc-tabanlı: banttaki ortalama+max pc (şiddetin geometri+miss birleşimi)."""
    ps=[p for m,p in rows if p is not None and p>0]
    if not ps: return 0.0,0.0
    import statistics
    return statistics.mean(ps), max(ps)

def main():
    cat=md._load_catalog(); norad_alt={c["norad"]:c["alt"] for c in cat}
    span=126.9
    conj=load_conj(norad_alt)
    print(f"Katalog {len(cat)} nesne | conjunction (irtifa-eşleşen) {len(conj)}\n")

    refs=[("Dusuk-LEO ~500km",500,None),("SSO ~800km peak",800,98.6),
          ("Starlink ~550km",550,53.0),("SSO ~750km",750,98.0),
          ("Yuksek-LEO ~1000km",1000,99.0),("SSO ~600km",600,97.8)]

    # F için bant conj sayımı (bucket)
    bc=Counter(int(a//HALF//2) for (a,m,p) in conj)

    print(f"{'Yorunge':<20}{'N_thr':>6}{'Lam/yil':>10}{'conj':>6}{'kappa':>7}{'medMiss':>8}{'Sa':>6}{'meanPc':>9}{'OCBI':>11}")
    print("-"*93)
    rows_out=[]
    for nm,alt,inc in refs:
        st=md._catalog_band_stats(cat,alt,HALF,inc,INC_TOL)
        N=st["non_maneuverable"]; vol=shell_volume(alt,HALF); rho=N/vol if vol>0 else 0.0
        lam=rho*v_rel(alt)*A_REF_KM2*SEC_PER_YEAR

        st_iso=md._catalog_band_stats(cat,alt,HALF,None,INC_TOL); band_obj=st_iso["total"]
        conj_obs=bc.get(int(alt//HALF//2),0)
        obj_days=band_obj*span if band_obj else 0
        F_obs=(conj_obs/obj_days) if obj_days>0 else 0.0
        F_exp=lam/365.0
        kappa=(F_obs/F_exp) if F_exp>0 else 1.0

        bs=band_slice(conj,alt)
        Sa,medMiss=severity_strict(bs)
        meanPc,maxPc=severity_pc(bs)

        C=0.0; T=0.0
        OCBI=lam*max(kappa,1e-9)*(1+ALPHA*Sa)*(1+BETA*C)*(1+GAMMA*T)
        rows_out.append((nm,alt,N,lam,conj_obs,kappa,medMiss,Sa,meanPc,OCBI))

    for r in sorted(rows_out,key=lambda x:-x[9]):
        nm,alt,N,lam,co,ka,mm,Sa,mp,ocbi=r
        mms=f"{mm:.0f}" if mm is not None else "-"
        print(f"{nm:<20}{N:>6}{lam:>10.2e}{co:>6}{ka:>7.2f}{mms:>8}{Sa:>6.2f}{mp:>9.2e}{ocbi:>11.2e}")
    print("-"*93)
    print("\nS_a=sıkı-miss CDF {200/100/50/25m}, meanPc=bant ort Pc. Hangisi daha iyi ayrım veriyor bak.")
    print("κ: fizik-Λ ile gözlem-F oranı (~1 civarı=uyum). med Miss: bant medyan yakınlık (küçük=şiddetli).")

if __name__=="__main__": main()
