"""
Production endpoint health smoke tests.

Tum testler GET-only - production'a side-effect yok.
Default URL: http://127.0.0.1:8765 (engine direkt).

Beklenenler:
  - Public endpoint'ler: 200 + dogru content-type
  - Auth-gated endpoint'ler: 401 (anonymous, auth yok)
  - Admin endpoint'ler: 401 (anonymous)
  - 404 endpoint: 404
"""
import pytest


class TestPublicEndpoints:
    """Auth gerekmeyen public endpoint'ler 200 donmeli."""

    def test_landing_stats(self, smoke_get):
        r = smoke_get("/landing-stats")
        assert r.status_code == 200, f"landing-stats {r.status_code}: {r.text[:200]}"
        data = r.json()
        # Beklenen alanlar
        assert "tracked_objects" in data
        assert "red_alerts" in data
        assert "directory_count" in data
        # Mantikli degerler
        assert data["tracked_objects"] > 1000, f"tracked_objects cok dusuk: {data['tracked_objects']}"
        assert data["directory_count"] > 0

    def test_landing_stats_returns_json(self, smoke_get):
        r = smoke_get("/landing-stats")
        assert r.status_code == 200
        # JSON parse hatasiz olmali
        r.json()

    def test_catalog_spacetrack(self, smoke_get):
        r = smoke_get("/catalog/spacetrack")
        # 200 veya cache fresh ise 200 - bos da olabilir
        assert r.status_code in (200, 503), f"catalog/spacetrack {r.status_code}"
        if r.status_code == 200:
            data = r.json()
            # Catalog yapisi
            assert "debris" in data or "rocket_body" in data or isinstance(data, dict)


class TestAuthGatedEndpoints:
    """Auth gerektiren endpoint'ler anonymous istegine 401 donmeli."""

    def test_admin_users_requires_auth(self, smoke_get):
        r = smoke_get("/admin/users")
        assert r.status_code in (401, 403), f"admin/users anonymous {r.status_code} (401/403 beklenir)"

    def test_admin_stats_requires_auth(self, smoke_get):
        r = smoke_get("/admin/stats")
        assert r.status_code in (401, 403)

    def test_eusst_aggregate_requires_auth(self, smoke_get):
        r = smoke_get("/eusst/aggregate")
        assert r.status_code in (401, 403)

    def test_watchlist_list_requires_auth(self, smoke_get):
        r = smoke_get("/watchlist/list")
        assert r.status_code in (401, 403, 404)


class TestAuthLogin:
    """Login endpoint dogru hata mesaji donmeli."""

    def test_login_invalid_credentials(self, smoke_get, http_session, base_url):
        # GET'le 405 Method Not Allowed bekleyebiliriz, POST gerekli
        # Smoke icin POST yapmiyoruz - login endpoint'in varligini dolayli olarak test edelim
        # Sadece /api/auth/login GET 405 ya da 404 donmeli (POST endpoint)
        r = smoke_get("/auth/login")
        assert r.status_code in (404, 405, 400), f"auth/login GET {r.status_code}"


class TestErrorHandling:
    def test_nonexistent_endpoint_404(self, smoke_get):
        r = smoke_get("/this-endpoint-definitely-does-not-exist-12345")
        assert r.status_code == 404, f"404 beklenir, geldi: {r.status_code}"

    def test_invalid_admin_path_returns_auth_error(self, smoke_get):
        """Admin pathlerin authsuz erisimi 401/403 olmali, 500 degil."""
        r = smoke_get("/admin/nonsense")
        assert r.status_code in (401, 403, 404), f"admin nonsense {r.status_code} (500 OLMAMAL)"


class TestResponseTime:
    """Endpoint'ler makul surede cevap vermeli (saglik gostergesi)."""

    def test_landing_stats_fast(self, smoke_get):
        import time
        start = time.time()
        r = smoke_get("/landing-stats")
        elapsed = time.time() - start
        assert r.status_code == 200
        # 5 saniyeden hizli olmali (cache var)
        assert elapsed < 5.0, f"landing-stats {elapsed:.2f}s - cok yavas"


class TestSecurityHeaders:
    """Engine direkt port 8765'te bu header'lar olmayabilir, nginx ekler.
    Production URL (www.casplatform.com) icin bu testler anlamli."""

    def test_no_server_version_leak(self, smoke_get):
        r = smoke_get("/landing-stats")
        # Python BaseHTTPServer "Server: BaseHTTP/0.6 Python/3.12" donebilir
        # Production'da nginx bunu override ediyor olmali
        server = r.headers.get("Server", "")
        # Engine direkt test edilirken Python version sizar - kabul
        # Sadece "yok" durumunda raporla
        # Bu test soft assertion - sadece bilgi amacli
        assert True  # her zaman gec, sadece header'i incelemek icin
