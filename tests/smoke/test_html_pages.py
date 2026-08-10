"""
HTML page smoke tests.

Static dosyalar nginx tarafindan serv edilir, engine port 8765'te erisilemez.
Bu testler PROD URL (www.casplatform.com) gerektirir.

SMOKE_BASE_URL=http://127.0.0.1:8765 olarak ayarliysa skip.
"""
import os
import pytest


def _is_local():
    base = os.environ.get("SMOKE_BASE_URL", "http://127.0.0.1:8765")
    return "127.0.0.1" in base or "localhost" in base


pytestmark = pytest.mark.skipif(
    _is_local(),
    reason="HTML page testleri prod URL gerektirir (engine port 8765 static dosya servmiyor)"
)


class TestLandingPage:
    def test_landing_returns_200(self, smoke_get):
        r = smoke_get("/")
        assert r.status_code == 200, f"landing {r.status_code}"

    def test_landing_has_html_content(self, smoke_get):
        r = smoke_get("/")
        assert r.status_code == 200
        content = r.text.lower()
        # Beklenen icerik (positioning)
        assert "cas" in content or "casplatform" in content
        assert "<html" in content

    def test_landing_has_pricing(self, smoke_get):
        r = smoke_get("/")
        # Pricing kismi olmali (Starter $999 / Pro $1,999)
        assert "999" in r.text or "starter" in r.text.lower()


class TestPortalPage:
    def test_portal_returns_200(self, smoke_get):
        r = smoke_get("/portal.html")
        assert r.status_code == 200

    def test_portal_has_html(self, smoke_get):
        r = smoke_get("/portal.html")
        assert "<html" in r.text.lower() or "<!doctype" in r.text.lower()
