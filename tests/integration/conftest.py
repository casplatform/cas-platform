"""
Integration test fixtures.

Strateji:
- Production casdb kullanilir (schema gercek)
- Her test bir transaction acar, SONUNDA ROLLBACK -> hicbir veri yazilmaz
- test_user fixture: izole email pattern (pytest-*@cas.test), garantili cleanup
- DİKKAT: outer tests/conftest.py'deki autouse monkeypatch (DB_URL bozuyor)
  burada override edilir; integration testler gercek DB'ye baglanir
"""
import os
import sys
import time
import secrets
import pytest
import psycopg2

# /opt/cas'i path'e ekle (cas_engine importu icin)
sys.path.insert(0, "/opt/cas")

# ── .env'den DB_URL yukle ──
def _load_env():
    env_path = "/opt/cas/.env"
    if not os.path.exists(env_path):
        return
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            v = v.strip().strip('"').strip("'")
            os.environ.setdefault(k.strip(), v)

_load_env()
_REAL_DB_URL = os.environ.get("DB_URL", "")
if not _REAL_DB_URL or "invalid" in _REAL_DB_URL:
    pytest.exit("Integration testleri icin gercek DB_URL gerekli. /opt/cas/.env kontrol edin.", returncode=2)


# ──────────────────────────────────────────────────────
# 1. autouse_db_real: outer conftest'in DB_URL bozmasini override et
# ──────────────────────────────────────────────────────
@pytest.fixture(autouse=True)
def restore_real_db(monkeypatch):
    """
    Outer tests/conftest.py'de autouse=True bir fixture DB_URL'i bozuyor.
    Integration testlerde gercek DB lazim - burada restore edilir.
    Bu fixture autouse oldugu icin her integration testte calisir.
    """
    monkeypatch.setenv("DB_URL", _REAL_DB_URL)
    yield


# ──────────────────────────────────────────────────────
# 2. db_conn: her test fresh connection + final ROLLBACK
# ──────────────────────────────────────────────────────
@pytest.fixture
def db_conn():
    """
    Test transaction'i. Hicbir COMMIT yapilmaz -> veri yazilmaz.
    NOT: psycopg2 default autocommit=False; transaction acik kalir.
    """
    conn = psycopg2.connect(_REAL_DB_URL)
    conn.autocommit = False
    yield conn
    try:
        conn.rollback()
    except Exception:
        pass
    conn.close()


# ──────────────────────────────────────────────────────
# 3. db_committed: COMMIT gereken testler icin + manual cleanup
# ──────────────────────────────────────────────────────
@pytest.fixture
def db_committed():
    """
    AUTH.register() gibi commit yapan modulleri test ederken kullanilir.
    Cleanup: created_emails listesindeki tum kullanicilar DELETE edilir.
    """
    conn = psycopg2.connect(_REAL_DB_URL)
    conn.autocommit = True
    created_emails = []
    
    class Tracker:
        def __init__(self):
            self.emails = created_emails
        def track(self, email):
            created_emails.append(email)
    
    yield Tracker()
    
    # Cleanup - cascade temizlik (delete_user gibi sirayla)
    cur = conn.cursor()
    for email in created_emails:
        try:
            cur.execute("SELECT id FROM users WHERE email=%s", (email,))
            row = cur.fetchone()
            if row:
                uid = row[0]
                cur.execute("DELETE FROM user_activity WHERE user_id=%s", (uid,))
                cur.execute("DELETE FROM notification_prefs WHERE user_id=%s", (uid,))
                cur.execute("DELETE FROM watchlist_results WHERE user_id=%s", (uid,))
                cur.execute("DELETE FROM decision_results WHERE user_id=%s", (uid,))
                cur.execute("DELETE FROM login_log WHERE user_id=%s", (uid,))
                cur.execute("DELETE FROM admin_log WHERE admin_id=%s", (uid,))
                cur.execute("DELETE FROM users WHERE id=%s", (uid,))
        except Exception as e:
            print(f"[cleanup warning] {email}: {e}")
    cur.close()
    conn.close()


# ──────────────────────────────────────────────────────
# 4. test_email: izole email factory
# ──────────────────────────────────────────────────────
@pytest.fixture
def test_email():
    """
    Pattern: pytest-<random>@cas.test
    Production'da bu pattern hicbir zaman kullanilmaz - izolasyon garantisi.
    """
    return "pytest-" + secrets.token_hex(8) + "@cas.test"


# ──────────────────────────────────────────────────────
# 5. auth_mgr / admin_mgr: cas_engine global instance'lari
# ──────────────────────────────────────────────────────
@pytest.fixture
def auth_mgr():
    """cas_engine.AUTH global instance'i. Engine restart gerektirmez."""
    from cas_engine import AUTH
    return AUTH


@pytest.fixture
def admin_mgr():
    """cas_engine.ADMIN global instance'i."""
    from cas_engine import ADMIN
    return ADMIN


# ──────────────────────────────────────────────────────
# 6. existing_admin_id: prod admin user (mustafa@casplatform.com)
# ──────────────────────────────────────────────────────
@pytest.fixture
def existing_admin_id():
    """
    Mevcut admin user'in ID'si - delete/toggle gibi testlerde
    'kim cagiriyor' parametresi olarak kullanilir.
    """
    conn = psycopg2.connect(_REAL_DB_URL)
    cur = conn.cursor()
    cur.execute("SELECT id FROM users WHERE role='admin' ORDER BY id LIMIT 1")
    row = cur.fetchone()
    cur.close()
    conn.close()
    if not row:
        pytest.skip("Sistemde admin user yok")
    return row[0]


# ──────────────────────────────────────────────────────
# 7. created_test_user: bir test user create eder, sonra siler
# ──────────────────────────────────────────────────────
@pytest.fixture
def created_test_user(db_committed, test_email):
    """
    AUTH.register ile gercek bir user create eder, email_verified=true yapar.
    Test sonu cleanup otomatik (db_committed tracker).
    
    Donen: {"id": int, "email": str, "password": str, "api_key": str}
    """
    from cas_engine import AUTH
    password = "TestPass123!"
    result, err = AUTH.register(test_email, password, "Test User")
    if err:
        pytest.fail(f"Test user create edilemedi: {err}")
    
    # email_verified = true yap (login icin gerekli)
    conn = psycopg2.connect(_REAL_DB_URL)
    conn.autocommit = True
    cur = conn.cursor()
    # login() requires is_active=true as well (see cas_engine AUTH.login);
    # the verify-email flow sets both, so a test user must mirror that.
    cur.execute("UPDATE users SET email_verified=true, is_active=true WHERE email=%s", (test_email,))
    cur.execute("SELECT id, api_key FROM users WHERE email=%s", (test_email,))
    row = cur.fetchone()
    cur.close()
    conn.close()
    
    db_committed.track(test_email)
    
    return {
        "id": row[0],
        "email": test_email,
        "password": password,
        "api_key": row[1],
    }


# ──────────────────────────────────────────────────────
# 8. admin_id_fixture: existing_admin_id alias (test_admin.py uyumu)
# ──────────────────────────────────────────────────────
@pytest.fixture
def admin_id_fixture(existing_admin_id):
    """test_admin.py icin existing_admin_id alias'i."""
    return existing_admin_id


# ──────────────────────────────────────────────────────
# 9. watchlist_mgr: cas_engine.WATCHLIST global instance
# ──────────────────────────────────────────────────────
@pytest.fixture
def watchlist_mgr():
    from cas_engine import WATCHLIST
    return WATCHLIST
