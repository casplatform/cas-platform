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
    return _base_url()


def _base_url():
    return os.environ.get("SMOKE_BASE_URL", "http://127.0.0.1:8765")


# ── Which instance is under test, and which questions apply to it ─────────
#
# This suite answers two different questions and they have different right
# answers on different instances:
#
#   "is the code correct"       -- does /admin/users still refuse an anonymous
#                                  caller, is /landing-stats valid JSON, does a
#                                  report still download. A property of the
#                                  commit, so it must be asked of the instance
#                                  running that commit.
#   "is the live install well"  -- did the CDM cron run, is the catalog cache
#                                  fresh, is any data source stale. A property
#                                  of a deployment, and only of the deployment
#                                  that has cron: production.
#
# They used to be one set aimed at one place, which is why deploy.sh's test gate
# measured production while the whole point of the gate is to judge the commit
# about to replace it -- the same shape as the finding that pytest was testing
# production's files, a week earlier.
#
# Staging is the instance where the second question has no meaning at all.
# It deliberately runs no cron (CLAUDE.md: manual instance, and the Space-Track
# quota is shared), so its data is frozen by design -- casdb_staging's newest
# CDM is from the day it was copied. Asking "is the data fresh" there is not a
# stricter test, it is a wrong one, and it would fail every deploy for a reason
# that has nothing to do with the commit.
_STAGING_PORTS = (":8775", ":8776")


def _target():
    """'staging' or 'production'. SMOKE_TARGET overrides the inference."""
    explicit = os.environ.get("SMOKE_TARGET", "").strip().lower()
    if explicit in ("staging", "production"):
        return explicit
    url = _base_url()
    return "staging" if any(p in url for p in _STAGING_PORTS) else "production"


@pytest.fixture(scope="session")
def smoke_target():
    return _target()


@pytest.fixture
def production_only(smoke_target):
    """Skip a deployment-health test when the target is not production."""
    if smoke_target != "production":
        pytest.skip(
            "deployment-health check, target is %s -- staging has no cron by "
            "design, so its data is frozen and this asks nothing about the "
            "commit" % smoke_target)


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
