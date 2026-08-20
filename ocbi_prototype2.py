#!/usr/bin/env python3
"""OCBI Fizik-Çekirdeği v2 — TEHDİT-tabanlı yoğunluk (manevra-yapamaz debris+RB).
Ham sayım yerine non-maneuverable nesne akısı = gerçek katastrofik-çarpışma yükü."""
import os, sys, math
_CAS_HOME = os.environ.get("CAS_HOME", "/opt/cas").rstrip("/") or "/opt/cas"
sys.path.insert(0, _CAS_HOME); sys.path.insert(0, os.path.join(_CAS_HOME, "cas_api"))
from cas_api.services import mission_design as md

MU=398600.4418; RE=6378.137; A_REF_KM2=1e-5; SEC_PER_YEAR=3.15576e7

def shell_volume(center, half):
    R_lo, R_hi = RE+(center-half), RE+(center+half)
    return (4.0/3.0)*math.pi*(R_hi**3 - R_lo**3)

def v_rel(center):
    v_c=math.sqrt(MU/(RE+center)); return (4.0/math.pi)*v_c, v_c

def lam(catalog, center, half=25.0, inc=None, threat_only=True):
    stats=md._catalog_band_stats(catalog, center, half, inc, 5.0)
    N = stats["non_maneuverable"] if threat_only else stats["total"]
    vol=shell_volume(center, half); rho = N/vol if vol>0 else 0.0
    vr,vc=v_rel(center)
    rate=rho*vr*A_REF_KM2*SEC_PER_YEAR
    return N, stats["total"], rho, vr, stats["debris_fraction_pct"], rate

def main():
    cat=md._load_catalog()
    print(f"Katalog: {len(cat)} LEO nesnesi\n")
    refs=[("Dusuk-LEO ~500km",500,None),("SSO ~800km peak",800,98.6),
          ("Starlink ~550km",550,53.0),("SSO ~750km",750,98.0),
          ("Yuksek-LEO ~1000km",1000,99.0),("SSO ~600km",600,97.8)]
    print("=== TEHDIT-tabanli (manevra-yapamaz debris+RB), IZOTROPIK ===")
    print(f"{'Yorunge':<22}{'N_thr':>6}{'N_tot':>6}{'deb%':>6}{'rho_thr':>11}{'Lambda/yil':>12}{'~yil':>9}")
    print("-"*73)
    lams=[]
    for nm,alt,inc in refs:
        Nt,Ntot,rho,vr,deb,rate=lam(cat,alt,inc=None,threat_only=True)  # izotropik
        lams.append(rate)
        yb=f"{1/rate:,.0f}" if rate>0 else "inf"
        print(f"{nm:<22}{Nt:>6}{Ntot:>6}{deb:>6.1f}{rho:>11.2e}{rate:>12.2e}{yb:>9}")
    print("-"*73)
    pos=[l for l in lams if l>0]
    print(f"max/min = {max(pos)/min(pos):.0f}×  (beklenti 100-1000×)\n")

    print("=== TEHDIT-tabanli + EGIM-KAPILI (SSO'lar icin gercek kesisim) ===")
    print(f"{'Yorunge':<22}{'N_thr':>6}{'deb%':>6}{'rho_thr':>11}{'Lambda/yil':>12}{'~yil':>9}")
    print("-"*66)
    lams2=[]
    for nm,alt,inc in refs:
        Nt,Ntot,rho,vr,deb,rate=lam(cat,alt,inc=inc,threat_only=True)  # egim-kapili
        lams2.append(rate)
        yb=f"{1/rate:,.0f}" if rate>0 else "inf"
        print(f"{nm:<22}{Nt:>6}{deb:>6.1f}{rho:>11.2e}{rate:>12.2e}{yb:>9}")
    print("-"*66)
    pos2=[l for l in lams2 if l>0]
    print(f"max/min = {max(pos2)/min(pos2):.0f}×")
    print("\nBEKLENTI: SSO ~750-900km EN YUKSEK olmali; 500-550km dusuk (drag+aktif payload).")

if __name__=="__main__": main()
