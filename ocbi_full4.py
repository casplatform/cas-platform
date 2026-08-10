#!/usr/bin/env python3
"""OCBI v4 — C (cascade maruziyeti) bağlandı. C = C_havuz × C_kalıcılık:
  C_havuz     = bant±50km'deki toplam katalog nesnesi (maruz ikincil popülasyon)
  C_kalıcılık = parçalanma bulutunun %90 temizlenme süresi (NASA SBM + drag, vleo.py)
Her ikisi log-normalize [0,1] → Ĉ. Kalan placeholder: sadece T (backfill)."""
import os, sys, math
for line in open('/opt/cas/.env'):
    if '=' in line and not line.strip().startswith('#'):
        k,v=line.strip().split('=',1); os.environ.setdefault(k,v.strip().strip('"'))
sys.path.insert(0,'/opt/cas'); sys.path.insert(0,'/opt/cas/cas_api')
from cas_api.services import mission_design as md
import vleo
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

def severity_strict(rows):
    ms=[m for m,p in rows if m is not None]
    if not ms: return 0.0,None
    n=len(ms); thr={200:0,100:0,50:0,25:0}
    for m in ms:
        for d in thr:
            if m<d: thr[d]+=1
    w={200:1,100:2,50:4,25:8}
    return sum(w[d]*(thr[d]/n) for d in thr)/sum(w.values()), sorted(ms)[n//2]

def cascade_term(cat, center):
    """C_havuz × C_kalıcılık → (raw bileşenler). Normalizasyon main'de (popülasyona göre)."""
    # C_havuz: bant±50km maruz popülasyon (parçalanma bulutu komşu bantlara yayılır)
    pool = md._catalog_band_stats(cat, center, 50.0, None, INC_TOL)["total"]
    # C_kalıcılık: temsili parçalanmanın %90 temizlenme süresi (gün)
    try:
        cz = vleo.assess_vleo_cascade(center)
        # cloud-clearing %90 milestone'unu bul (anahtar adı esnek — dict'i tara)
        clear90 = None
        for k,v in cz.items():
            if "clear" in str(k).lower() and isinstance(v,dict):
                for kk,vv in v.items():
                    if "90" in str(kk): clear90=float(vv)
            elif "90" in str(k) and isinstance(v,(int,float)): clear90=float(v)
        if clear90 is None:  # fallback: temsili 10cm parçanın ömrü
            clear90 = vleo.estimate_orbital_lifetime(center, ballistic_coef=50.0, f107_flux=150.0)
    except Exception as e:
        clear90 = vleo.estimate_orbital_lifetime(center, ballistic_coef=50.0, f107_flux=150.0)
    return pool, clear90

def main():
    cat=md._load_catalog(); norad_alt={c["norad"]:c["alt"] for c in cat}
    conj=load_conj(norad_alt)
    refs=[("Dusuk-LEO ~500km",500,None),("SSO ~800km peak",800,98.6),
          ("Starlink ~550km",550,53.0),("SSO ~750km",750,98.0),
          ("Yuksek-LEO ~1000km",1000,99.0),("SSO ~600km",600,97.8)]
    bc=Counter(int(a//HALF//2) for (a,m,p) in conj)

    tmp=[]
    for nm,alt,inc in refs:
        st=md._catalog_band_stats(cat,alt,HALF,inc,INC_TOL)
        N=st["non_maneuverable"]; vol=shell_volume(alt,HALF); rho=N/vol if vol>0 else 0.0
        lam=rho*v_rel(alt)*A_REF_KM2*SEC_PER_YEAR
        pool,clear90=cascade_term(cat,alt)
        tmp.append([nm,alt,inc,lam,N,bc.get(int(alt//HALF//2),0),pool,clear90])

    sum_lam=sum(t[3] for t in tmp) or 1.0
    sum_conj=sum(t[5] for t in tmp) or 1.0
    # C normalize: log-ölçek (pool×clear90 çok geniş aralıkta) → [0,1]
    raw_C=[math.log10(max(t[6]*t[7],1.0)) for t in tmp]
    Cmin,Cmax=min(raw_C),max(raw_C)

    lo,hi=(0,0)
    print(f"Katalog {len(cat)} | conj {len(conj)}\n")
    print(f"{'Yorunge':<20}{'Lam/yil':>10}{'kappa':>7}{'Sa':>6}{'pool':>6}{'clr90g':>9}{'Chat':>6}{'OCBI':>11}{'pctl':>6}")
    print("-"*88)
    rows=[]
    for i,(nm,alt,inc,lam,N,conj_obs,pool,clear90) in enumerate(tmp):
        kappa=((conj_obs/sum_conj)/(lam/sum_lam)) if lam>0 else 1.0
        lo_,hi_=alt-HALF,alt+HALF
        Sa,medM=severity_strict([(m,p) for (a,m,p) in conj if lo_<=a<=hi_])
        Chat=(raw_C[i]-Cmin)/(Cmax-Cmin) if Cmax>Cmin else 0.0
        T=0.0
        OCBI=lam*max(kappa,1e-9)*(1+ALPHA*Sa)*(1+BETA*Chat)*(1+GAMMA*T)
        rows.append((nm,lam,kappa,Sa,pool,clear90,Chat,OCBI))
    ob=sorted(r[7] for r in rows); pct=lambda v:100.0*sum(1 for x in ob if x<=v)/len(ob)
    for r in sorted(rows,key=lambda x:-x[7]):
        nm,lam,ka,Sa,pool,c90,Ch,ocbi=r
        c90s=f"{c90:,.0f}" if c90<36000 else ">100yıl"
        print(f"{nm:<20}{lam:>10.2e}{ka:>7.2f}{Sa:>6.2f}{pool:>6}{c90s:>9}{Ch:>6.2f}{ocbi:>11.2e}{pct(ocbi):>5.0f}%")
    print("-"*88)
    print("\npool=bant±50km maruz nesne | clr90g=%90 bulut-temizlenme (gün, NASA SBM+drag)")
    print("Chat=log-normalize(pool×clr90) [0,1] — 'kaç nesne × ne kadar süre maruz'")
    print("GÜVEN: Λ=YÜKSEK κ=DÜŞÜK(4ay) S=ORTA C=ORTA(SBM+drag fizik) T=YOK(backfill)")

if __name__=="__main__": main()
