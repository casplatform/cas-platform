#!/usr/bin/env python3
"""
OCBI TAM İSKELET (Yol A — 4 aylık veriyle, güven-etiketli)
==========================================================
OCBI = Λ_phys · κ · (1+α·Ŝ) · (1+β·Ĉ) · (1+γ·T̂)  + katalog persentili
  Λ_phys = ρ_threat · v̄_rel · A_ref   (fizik, DOĞRULANDI)
  κ       = F_gözlem / F_beklenen       (ampirik kalibrasyon)
  S       = şiddet (miss-distance CDF)
  C       = cascade maruziyeti          (placeholder → cascade modülü sonra)
  T       = trend                       (placeholder → backfill sonra)
STANDALONE, read-only. Her terime GÜVEN-ETİKETİ (4-ay=DÜŞÜK, backfill sonrası=YÜKSEK).
"""
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
HALF=25.0; INC_TOL=5.0
# kompozit katsayıları (dürüst default'lar — underwriter override edebilir)
ALPHA=1.0; BETA=0.5; GAMMA=0.5

def shell_volume(c,h): 
    return (4/3)*math.pi*((RE+c+h)**3-(RE+c-h)**3)
def v_rel(c):
    vc=math.sqrt(MU/(RE+c)); return (4/math.pi)*vc

def db():
    return psycopg2.connect(os.environ["DB_URL"])

# ---------- VERİ SAĞLIK KONTROLÜ ----------
def data_health():
    conn=db(); cur=conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("""SELECT count(*) n,
        count(miss_dist_m) miss_filled, count(pc) pc_filled,
        min(fetched_at)::date mn, max(fetched_at)::date mx,
        EXTRACT(EPOCH FROM (max(fetched_at)-min(fetched_at)))/86400.0 days,
        min(miss_dist_m) miss_min, max(miss_dist_m) miss_max,
        percentile_cont(0.5) WITHIN GROUP (ORDER BY miss_dist_m) miss_med,
        min(pc) pc_min, max(pc) pc_max FROM conjunction_events""")
    r=cur.fetchone()
    print("="*70); print("VERİ SAĞLIK KONTROLÜ (S/F terimleri için)"); print("="*70)
    print(f"  Toplam kayıt: {r['n']}  |  span: {r['days']:.1f} gün ({r['mn']}→{r['mx']})")
    print(f"  miss_dist_m dolu: {r['miss_filled']}/{r['n']} ({100*r['miss_filled']/r['n']:.0f}%)  "
          f"aralık [{r['miss_min']:.0f}, {r['miss_max']:.0f}]m  medyan {r['miss_med']:.0f}m" if r['miss_filled'] else "  miss_dist_m: BOŞ")
    print(f"  pc dolu: {r['pc_filled']}/{r['n']} ({100*r['pc_filled']/r['n']:.0f}%)  "
          f"aralık [{r['pc_min']:.2e}, {r['pc_max']:.2e}]" if r['pc_filled'] else "  pc: BOŞ")
    # miss-distance eşik dağılımı (S için)
    print("\n  Miss-distance eşik dağılımı (S = şiddet CDF):")
    for d in [5000,1000,500,100]:
        cur.execute("SELECT count(*) c FROM conjunction_events WHERE miss_dist_m < %s", (d,))
        c=cur.fetchone()["c"]
        print(f"    miss < {d:>5}m : {c:>6} ({100*c/r['n']:.1f}%)")
    cur.close(); conn.close()
    return r['days']

# ---------- F: bant-bazlı gözlenen yakın-geçiş oranı ----------
def band_conjunction_map(norad_alt, span_days):
    conn=db(); cur=conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("SELECT norad1,norad2 FROM conjunction_events")
    bc=Counter()
    for row in cur.fetchall():
        alts=[norad_alt.get(str(row["norad1"]).strip()), norad_alt.get(str(row["norad2"]).strip())]
        alts=[a for a in alts if a is not None]
        if alts:
            bc[int(sum(alts)/len(alts)//HALF//2)] += 1  # HALF*2 genişliğinde bucket index
    cur.close(); conn.close()
    return bc

# ---------- S: bir bant için şiddet indeksi ----------
def severity_for_band(norad_alt, center):
    """S = ağırlıklı miss-distance CDF, o banttaki conjunction'lar için."""
    lo,hi=center-HALF,center+HALF
    conn=db(); cur=conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("SELECT norad1,norad2,miss_dist_m FROM conjunction_events WHERE miss_dist_m IS NOT NULL")
    thr={5000:0,1000:0,500:0,100:0}; n=0; mind=None
    for row in cur.fetchall():
        alts=[norad_alt.get(str(row["norad1"]).strip()), norad_alt.get(str(row["norad2"]).strip())]
        alts=[a for a in alts if a is not None]
        if not alts: continue
        if lo<=sum(alts)/len(alts)<=hi:
            n+=1; m=row["miss_dist_m"]
            mind=m if mind is None else min(mind,m)
            for d in thr:
                if m<d: thr[d]+=1
    cur.close(); conn.close()
    if n==0: return 0.0, None, 0
    # ağırlıklı: yakın eşikler daha ağır (1,2,4,8)
    w={5000:1,1000:2,500:4,100:8}
    S=sum(w[d]*(thr[d]/n) for d in thr)/sum(w.values())
    return S, mind, n

# ---------- ana OCBI ----------
def main():
    span=data_health()
    cat=md._load_catalog()
    norad_alt={c["norad"]:c["alt"] for c in cat}

    refs=[("Dusuk-LEO ~500km",500,None),("SSO ~800km peak",800,98.6),
          ("Starlink ~550km",550,53.0),("SSO ~750km",750,98.0),
          ("Yuksek-LEO ~1000km",1000,99.0),("SSO ~600km",600,97.8)]

    # tüm bantlar için Λ_phys ve F topla (κ ve persentil için popülasyon)
    print("\n"+"="*70); print("TAM OCBI (Yol A, güven: F/κ=DÜŞÜK[4ay], S=ORTA, C/T=placeholder)"); print("="*70)
    bc = band_conjunction_map(norad_alt, span)

    rows=[]
    for nm,alt,inc in refs:
        st=md._catalog_band_stats(cat,alt,HALF,inc,INC_TOL)
        N=st["non_maneuverable"]; vol=shell_volume(alt,HALF)
        rho=N/vol if vol>0 else 0.0
        lam=rho*v_rel(alt)*A_REF_KM2*SEC_PER_YEAR              # Λ_phys /yıl

        # F_gözlem: banttaki conj / (bant-nesne-günü)
        st_iso=md._catalog_band_stats(cat,alt,HALF,None,INC_TOL)  # F için izotropik nesne sayısı
        band_obj=st_iso["total"]
        bucket=int(alt//HALF//2)
        conj_obs=bc.get(bucket,0)
        obj_days=band_obj*span if band_obj else 0
        F_obs=(conj_obs/obj_days) if obj_days>0 else 0.0         # conj / nesne-gün

        # F_beklenen: Λ_phys'ten türet (çarpışma/yıl → yakın-geçiş/nesne-gün ölçeğine oransal)
        # (κ göreli bir düzeltme; mutlak ölçek A_ref'e bağlı, κ oranı ölçek-bağımsız)
        F_exp = lam / 365.0                                     # kaba beklenen günlük oran proxy
        kappa = (F_obs/F_exp) if F_exp>0 else 1.0

        S,mind,ns=severity_for_band(norad_alt,alt)
        C=0.0  # placeholder (cascade modülü sonra)
        T=0.0  # placeholder (backfill sonra)
        Shat=S; Chat=C; That=T
        OCBI=lam*max(kappa,1e-9)*(1+ALPHA*Shat)*(1+BETA*Chat)*(1+GAMMA*That)
        rows.append({"name":nm,"alt":alt,"inc":inc,"N":N,"lam":lam,"F_obs":F_obs,
                     "conj_obs":conj_obs,"kappa":kappa,"S":S,"mind":mind,"ns":ns,"OCBI":OCBI})

    # persentil: OCBI'yi kendi aralarında sırala (backfill sonrası tüm-katalog olur)
    ocbis=sorted(r["OCBI"] for r in rows)
    def pct(v): return 100.0*(sum(1 for x in ocbis if x<=v)/len(ocbis))

    print(f"\n{'Yorunge':<20}{'N_thr':>6}{'Lam/yil':>10}{'conj':>6}{'F_obs':>10}{'kappa':>7}{'S':>6}{'min_m':>7}{'OCBI':>11}{'pctl':>6}")
    print("-"*95)
    for r in sorted(rows,key=lambda x:-x["OCBI"]):
        mind=f"{r['mind']:.0f}" if r['mind'] is not None else "-"
        print(f"{r['name']:<20}{r['N']:>6}{r['lam']:>10.2e}{r['conj_obs']:>6}{r['F_obs']:>10.2e}"
              f"{r['kappa']:>7.2f}{r['S']:>6.2f}{mind:>7}{r['OCBI']:>11.2e}{pct(r['OCBI']):>5.0f}%")
    print("-"*95)
    print("\nGÜVEN ETİKETLERİ:  Λ_phys=YÜKSEK(fizik+36K gözlem uyumlu) | "
          "κ/F=DÜŞÜK(4ay,289 NORAD) | S=ORTA | C=YOK(cascade sonra) | T=YOK(backfill sonra)")
    print("Persentil şu an SADECE bu 6 referans arası; backfill sonrası tüm-katalog dağılımına göre olacak.")
    print("YORUM: OCBI sıralaması Λ_phys+gözlem ile aynı deseni vermeli (SSO 750-900 tepede).")

if __name__=="__main__": main()
