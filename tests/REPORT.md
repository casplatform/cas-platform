# CAS Test Suite Report

**Son güncelleme:** 2026-05-28 20:49

## Test Pyramid

```
┌─────────────────────────────────────┐
│  SMOKE (production health)          │  → /opt/cas/run_tests.sh smoke
│  ~14 test, GET-only                 │
├─────────────────────────────────────┤
│  INTEGRATION (engine + DB)          │  → /opt/cas/run_tests.sh integration
│  ~135 test, gerçek DB               │
├─────────────────────────────────────┤
│  UNIT (algorithmic core)            │  → /opt/cas/run_tests.sh unit
│  134 test, < 3 saniye               │
└─────────────────────────────────────┘
                                          → /opt/cas/run_tests.sh all
```

## Sprint Geçmişi

| Sprint | Tamamlandı | İçerik |
|--------|-----------|--------|
| 1A | 27 May 2026 | Test altyapısı, conftest, fixtures, test_auth (17) |
| 1B | 28 May 2026 | test_admin (24+1skip) — create_user production bug bulundu |
| 2 | 28 May 2026 | test_watchlist (14), test_decision_logic (14, golden ref), test_vleo (9) |
| 3 | 28 May 2026 | test_data_integrity (21), test_eusst (19), test_catalog (15) |
| 4 | 28 May 2026 | Smoke tests + cron (günlük 04:00) |

## Production Bug'ları (test sürecinde yakalanan)

1. **JWT zayıf signature** (sha256+secret, 16 char) → HMAC-SHA256 standart JWT'ye çevrildi
2. **create_user INSERT kolon/değer uyumsuzluğu** → admin panel "CREATE USER" tamamen kırıkmış, düzeltildi

## Çalıştırma

```bash
# Tüm test pyramid
bash /opt/cas/run_tests.sh all

# Sadece bir katman
bash /opt/cas/run_tests.sh unit
bash /opt/cas/run_tests.sh integration
bash /opt/cas/run_tests.sh smoke

# Production smoke (Cloudflare + nginx zinciri dahil)
SMOKE_BASE_URL=https://www.casplatform.com bash /opt/cas/run_tests.sh smoke
```

## Cron

```
0 4 * * * /opt/cas/scripts/run_smoke_cron.sh
```

Log: `/var/log/cas_smoke.log`

## Sonraki adımlar

- ML Layer 1 (false positive reduction) entegrasyonu
- VLEO Faz 2 (ΔV forecast, urgency score)
- Mobile PWA roadmap
- (Opsiyonel) GitHub Actions CI workflow
