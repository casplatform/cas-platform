"""
Smoke test fixtures.

Default base URL: http://127.0.0.1:8765 (engine direkt, hizli, Cloudflare yok).
SMOKE_BASE_URL=https://www.casplatform.com ile prod URL'ye atilabilir.

DIKKAT: Bu testler GET-only, hicbir side-effect yok.
"""
import os
import pytest
import requests


@pytest.fixture(scope="session")
def base_url():
    """Test edilecek endpoint base URL'i."""
    return os.environ.get("SMOKE_BASE_URL", "http://127.0.0.1:8765")


@pytest.fixture(scope="session")
def http_session():
    """requests.Session - keep-alive + redirect follow."""
    s = requests.Session()
    s.headers.update({"User-Agent": "CAS-Smoke-Test/1.0"})
    return s


def _is_prod_url(base_url):
    """Cloudflare + nginx ardindaki prod URL mi (vs engine direkt port 8765)."""
    return base_url.startswith("https://") or base_url.startswith("http://www.")


def _normalize_path(path, base_url):
    """
    Engine kodu sadece /admin/, /eusst/, /watchlist/ vb. path'leri bekler.
    Nginx /api/ prefix'ini striper.

    Bu fonksiyon path'i moda gore normalize eder:
      - Prod URL: /api/admin/users    (nginx /api/ striper)
      - Local:    /admin/users        (engine direkt, prefix yok)

    Path'i her zaman /api/'siz tanimlariz, prod URL modunda /api/ eklenir.
    """
    # Eger path zaten /api/ ile basliyorsa dokunma (geriye uyumluluk)
    if path.startswith("/api/"):
        if _is_prod_url(base_url):
            return path  # zaten dogru
        else:
            return path[4:]  # /api/X -> /X (local engine icin)
    # Path /api/'siz - prod URL'de /api/ ekle
    if _is_prod_url(base_url):
        # Sadece API endpoint'leri prefix alir (HTML pages alaz)
        # Heuristic: / ile basliyor, .html ile bitmiyor, "/" tek slash degil
        if path == "/" or path.endswith(".html") or path.endswith(".css") or path.endswith(".js"):
            return path
        return "/api" + path
    return path


def _get(session, base_url, path, **kwargs):
    """GET wrapper - 10s timeout, follow_redirects True, path mode-aware."""
    normalized = _normalize_path(path, base_url)
    url = base_url.rstrip("/") + normalized
    kwargs.setdefault("timeout", 10)
    kwargs.setdefault("allow_redirects", True)
    return session.get(url, **kwargs)


@pytest.fixture
def smoke_get(http_session, base_url):
    """Test'lerde kullanilacak GET helper."""
    def _f(path, **kwargs):
        return _get(http_session, base_url, path, **kwargs)
    return _f
