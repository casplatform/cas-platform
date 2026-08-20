#!/usr/bin/env python3
"""
OCBI Fizik-Çekirdeği Prototipi (Orbital Collision-Burden Index)
================================================================
Λ_phys = ρ(h,i) · v̄_rel · A_ref  — kollektif çarpışma-yükü çekirdeği.
Yayınlanmış kinetik-teori yöntemi (arXiv 2606.17947) + mevcut CAS katalog motoru.
STANDALONE: mission_design.py'yi import eder, HİÇBİR ŞEY değiştirmez. Read-only.
Amaç: ρ·v̄_rel tarafını backfill'i beklemeden doğrulamak (F/S/C/T sonra eklenir).
"""
import os, sys, math
_CAS_HOME = os.environ.get("CAS_HOME", "/opt/cas").rstrip("/") or "/opt/cas"
sys.path.insert(0, _CAS_HOME)
sys.path.insert(0, os.path.join(_CAS_HOME, "cas_api"))

# Mevcut doğrulanmış motoru yeniden kullan (irtifa/eğim/katalog/yoğunluk)
from cas_api.services import mission_design as md

MU = 398600.4418        # km^3/s^2
RE = 6378.137           # km
A_REF_KM2 = 10.0 * 1e-6 # 10 m^2 = 1e-5 km^2  (referans kesit — uydu boyutundan bağımsız)
SEC_PER_YEAR = 3.15576e7

def volumetric_density(catalog, center_km, half_km, inc_deg=None, inc_tol=5.0):
    """ρ [nesne/km^3] — mevcut bant sayımını KABUK HACMİNE böler (1B→3B düzeltme)."""
    stats = md._catalog_band_stats(catalog, center_km, half_km, inc_deg, inc_tol)
    N = stats["total"]
    R_lo = RE + (center_km - half_km)
    R_hi = RE + (center_km + half_km)
    shell_vol = (4.0/3.0) * math.pi * (R_hi**3 - R_lo**3)   # km^3 (izotropik küresel kabuk)
    rho = N / shell_vol if shell_vol > 0 else 0.0
    return rho, N, shell_vol, stats

def mean_relative_velocity(center_km):
    """v̄_rel [km/s] — dairesel yörünge hızı v_c=√(μ/R). İzotropik karşılaşma
    yaklaşımı: ort bağıl hız ≈ (4/π)·v_c ≈ 1.27·v_c (Kessler/kinetik-teori).
    (Başlangıç yaklaşımı; conjunction_events geometrisiyle sonra kalibre edilir.)"""
    R = RE + center_km
    v_c = math.sqrt(MU / R)
    v_rel_iso = (4.0/math.pi) * v_c
    return v_rel_iso, v_c

def lambda_phys(catalog, center_km, half_km=25.0, inc_deg=None):
    """Λ_phys = ρ · v̄_rel · A_ref  → beklenen katastrofik çarpışma / yıl (referans-uydu)."""
    rho, N, vol, stats = volumetric_density(catalog, center_km, half_km, inc_deg)
    v_rel, v_c = mean_relative_velocity(center_km)      # km/s
    rate_per_sec = rho * v_rel * A_REF_KM2              # 1/s
    rate_per_year = rate_per_sec * SEC_PER_YEAR
    return {
        "altitude_km": center_km, "inclination_deg": inc_deg,
        "N_band": N, "band_half_km": half_km,
        "shell_volume_km3": vol,
        "rho_per_km3": rho,
        "v_circular_km_s": v_c, "v_rel_mean_km_s": v_rel,
        "debris_fraction_pct": stats["debris_fraction_pct"],
        "lambda_per_year": rate_per_year,
        "lambda_per_year_sci": f"{rate_per_year:.3e}",
        "expected_years_between": (1.0/rate_per_year) if rate_per_year > 0 else float('inf'),
    }

def main():
    catalog = md._load_catalog()
    print(f"Katalog: {len(catalog)} LEO nesnesi yüklendi (mission_design cache)\n")
    # arXiv 2606.17947 referans vakalarına yakın 3 yörünge (+ birkaç kıyas)
    refs = [
        ("Dusuk-LEO ~500km (drag-temizli)",       500, None),
        ("SSO ~800km (peak debris, ENVISAT bolgesi)", 800, 98.6),
        ("Starlink kabugu ~550km",                 550, 53.0),
        ("~750km SSO",                             750, 98.0),
        ("~1000km (yuksek-LEO)",                  1000, 99.0),
    ]
    print(f"{'Yorunge':<42} {'N':>5} {'rho[/km^3]':>12} {'v_rel[km/s]':>11} {'deb%':>6} {'Lambda/yil':>12} {'~yil-arasi':>11}")
    print("-"*104)
    rows = []
    for name, alt, inc in refs:
        r = lambda_phys(catalog, alt, half_km=25.0, inc_deg=inc)
        rows.append((name, r))
        yb = r["expected_years_between"]
        yb_s = f"{yb:,.0f}" if yb < 1e9 else "inf"
        print(f"{name:<42} {r['N_band']:>5} {r['rho_per_km3']:>12.3e} "
              f"{r['v_rel_mean_km_s']:>11.2f} {r['debris_fraction_pct']:>6.1f} "
              f"{r['lambda_per_year_sci']:>12} {yb_s:>11}")
    print("-"*104)
    # Relatif kıyas (arXiv: yörüngeler arası 100-1000× fark beklenir)
    lam = [r["lambda_per_year"] for _, r in rows if r["lambda_per_year"] > 0]
    if lam:
        print(f"\nMertebe kontrolu: max/min Lambda orani = {max(lam)/min(lam):.1f}× "
              f"(arXiv/SwissRe: yorungeler arasi 100-1000× beklenir)")
        print("Yorum: en yuksek yuk ~750-900km SSO'da olmali (peak debris); "
              "500-550km drag-temizli, dusuk yuk.")
    print("\nNOT: Bu SADECE fizik cekirdegi (rho·v_rel·A_ref). "
          "F/S/C/T (conjunction_events + backfill + cascade) SONRA eklenecek → tam OCBI.")
    print("A_ref=10m^2 sabit → 'kollektif yuk' (uydu degerinden bagimsiz), arXiv 'collective index'.")

if __name__ == "__main__":
    main()
