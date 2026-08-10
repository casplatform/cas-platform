#!/usr/bin/env python3
"""OCBI v3 — κ PAY-NORMALIZE (ölçek-bağımsız desen uyumu), S=sıkı-miss CDF.
κ_band = (gözlenen conj payı) / (fizik-Λ payı); ~1=uyum, sapma=κ yakalar."""
import os, sys, math, statistics
for line in open('/opt/cas/.env'):
    if '=' in line and not line.strip().startswith('#'):
        k,v=line.strip().split('=',1); os.environ.setdefault(k,v.strip().strip('"'))
sys.path.insert(0,'/opt/cas'); sys.path.insert(0,'/opt/cas/cas_api')
from cas_api.services import mission_design as md
import psycopg2, psycopg2.extras
from collections import Counter

MU=398600.4418; RE=6378.137; A_REF_KM2=1e-5; SEC_PER_YEAR=3.15576e7
HALF=25.0; INC_TOL=5.0; ALPHA=1.0; BETA=0.5; GAMMA=0.5
def shell_volume(c,h): return (4/3)*math.pi*((RE+c+h)**3-(RE+c-h)**3)
def v_rel(c): vc=math.sqrt(MU/(RE+c)); return (4/math.pi)*vc
def db(): return psycopg2.connect(os.environ["DB_URL"])

def load_conj(norad_alt):
    conn=db(); cur=conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("SELECT norad1,norad2,miss_dist_m,pc FROM conjunction_events")
    out=[]
    for r in cur.fetchall():
        alts=[norad_alt.get(str(r["norad1"]).strip()), norad_alt.get(str(r["norad2"]).strip())]
        alts=[a for a in alts if a is not None]
        if alts:
            out.append((sum(alts)/len(alts),
                        float(r["miss_dist_m"]) if r["miss_dist_m"] is not None else None,
                        float(r["pc"]) if r["pc"] is not None else None))
    cur.close(); conn.close(); return out

def band_slice(conj,center):
    lo,hi=center-HALF,center+HALF
    return [(m,p) for (a,m,p) in conj if lo<=a<=hi]

def severity_strict(rows):
    ms=[m for m,p in rows if m is not None]
    if not ms: return 0.0,None
    n=len(ms); thr={200:0,100:0,50:0,25:0}
    for m in ms:
        for d in thr:
            if m<d: thr[d]+=1
    w={200:1,100:2,50:4,25:8}
    return sum(w[d]*(thr[d]/n) for d in thr)/sum(w.values()), sorted(ms)[n//2]

def lam_phys(cat,center,inc):
    st=md._catalog_band_stats(cat,center,HALF,inc,INC_TOL)
    N=st["non_maneuverable"]; vol=shell_volume(center,HALF); rho=N/vol if vol>0 else 0.0
    return rho*v_rel(center)*A_REF_KM2*SEC_PER_YEAR, N

def main():
    cat=md._load_catalog(); norad_alt={c["norad"]:c["alt"] for c in cat}
    conj=load_conj(norad_alt)
    refs=[("Dusuk-LEO ~500km",500,None),("SSO ~800km peak",800,98.6),
          ("Starlink ~550km",550,53.0),("SSO ~750km",750,98.0),
          ("Yuksek-LEO ~1000km",1000,99.0),("SSO ~600km",600,97.8)]
    bc=Counter(int(a//HALF//2) for (a,m,p) in conj)

    # PAY-NORMALIZE için: her referansın Λ ve gözlenen-conj'unu topla, sonra normalize
    tmp=[]
    for nm,alt,inc in refs:
        lam,N=lam_phys(cat,alt,inc)
        conj_obs=bc.get(int(alt//HALF//2),0)
        tmp.append([nm,alt,inc,lam,N,conj_obs])
    sum_lam=sum(t[3] for t in tmp) or 1.0
    sum_conj=sum(t[5] for t in tmp) or 1.0

    print(f"Katalog {len(cat)} | conj {len(conj)}\n")
    print(f"{'Yorunge':<20}{'N_thr':>6}{'Lam/yil':>10}{'Lam%':>6}{'conj':>6}{'conj%':>6}{'kappa':>7}{'medM':>6}{'Sa':>6}{'OCBI':>10}")
    print("-"*95)
    rows=[]
    for nm,alt,inc,lam,N,conj_obs in tmp:
        lam_share=lam/sum_lam; conj_share=conj_obs/sum_conj
        kappa=(conj_share/lam_share) if lam_share>0 else 1.0   # PAY-NORMALIZE, ölçek-bağımsız
        bs=band_slice(conj,alt); Sa,medM=severity_strict(bs)
        C=0.0; T=0.0
        OCBI=lam*kappa*(1+ALPHA*Sa)*(1+BETA*C)*(1+GAMMA*T)
        rows.append((nm,alt,N,lam,lam_share,conj_obs,conj_share,kappa,medM,Sa,OCBI))
    # persentil
    ob=sorted(r[10] for r in rows)
    pct=lambda v:100.0*sum(1 for x in ob if x<=v)/len(ob)
    for r in sorted(rows,key=lambda x:-x[10]):
        nm,alt,N,lam,ls,co,cs,ka,mm,Sa,ocbi=r
        mms=f"{mm:.0f}" if mm is not None else "-"
        print(f"{nm:<20}{N:>6}{lam:>10.2e}{100*ls:>5.0f}%{co:>6}{100*cs:>5.0f}%{ka:>7.2f}{mms:>6}{Sa:>6.2f}{ocbi:>10.2e}  {pct(ocbi):>3.0f}%")
    print("-"*95)
    print("\nκ PAY-NORMALIZE: gözlenen-conj-payı / fizik-Λ-payı. κ≈1 → gözlem fiziği doğruluyor.")
    print("κ>1 → bant beklenenden ÇOK geçiş üretiyor (κ yakalar); κ<1 → az. medM=medyan miss (şiddet).")
    print("GÜVEN: Λ=YÜKSEK, κ=DÜŞÜK(4ay/289 NORAD/watchlist-örneklem), S=ORTA, C/T=YOK.")

if __name__=="__main__": main()
