# Smoke Tests

Production endpoint sağlığını doğrular. GET-only, side-effect yok.

## Çalıştırma

### Local mode (default - engine direkt port 8765)
```bash
bash /opt/cas/run_tests.sh smoke
# veya
cd /opt/cas && python3 -m pytest tests/smoke/ -v
```

### Production URL mode (Cloudflare + nginx + engine tam zincir)
```bash
SMOKE_BASE_URL=https://www.casplatform.com python3 -m pytest tests/smoke/ -v
```

## Cron entry

Günlük 04:00'te çalışır:
```
0 4 * * * /opt/cas/scripts/run_smoke_cron.sh
```

Çıktı `/var/log/cas_smoke.log` dosyasına yazılır. Fail durumunda exit code != 0
(istenirse watchdog/notify entegrasyonu eklenebilir).

## Test grupları

- **TestPublicEndpoints** — `/api/landing-stats`, `/catalog/spacetrack` 200 + JSON
- **TestAuthGatedEndpoints** — admin/eusst/watchlist anonymous → 401/403
- **TestAuthLogin** — login endpoint varlığı
- **TestErrorHandling** — 404 endpoint, geçersiz admin path 401/403 (500 değil)
- **TestResponseTime** — landing-stats <5s
- **TestLandingPage / TestPortalPage** — sadece prod URL modunda

## Hangi modda ne test edilir?

| Test | Local | Prod URL |
|------|-------|----------|
| API endpoint health | ✓ | ✓ |
| HTML pages | skip | ✓ |
| nginx routing | — | ✓ |
| Cloudflare cache | — | ✓ |
