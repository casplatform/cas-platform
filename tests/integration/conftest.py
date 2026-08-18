"""
Integration test fixtures.

Strateji:
- Ayri bir test veritabani kullanilir (casdb_test), production'a ASLA yazilmaz
- Sema production'dan alinir, veri bos baslar
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

# ── Instance koku ────────────────────────────────────────
# Bu conftest calisan kopyanin kendi agacinda duruyor; kod ve .env oradan
# okunur. Sabit bir yol yazmak, staging'in production'in cas_engine'ini import
# etmesi ve production .env'ini okumasi demekti.
# tests/integration/conftest.py -> ust ust iki dizin yukarisi instance koku.
_INSTANCE_ROOT = os.environ.get("CAS_HOME") or os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_INSTANCE_ROOT = _INSTANCE_ROOT.rstrip("/") or "/"

if _INSTANCE_ROOT not in sys.path:
    sys.path.insert(0, _INSTANCE_ROOT)

# ── .env'i oku ───────────────────────────────────────────
# Degerler ayrica dondurulur: ortamdaki DB_URL bir sentinel olabilir (asagiya
# bak), o durumda test veritabani dosyadaki degerden turetilir.
def _load_env(env_path):
    values = {}
    if not os.path.exists(env_path):
        return values
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            values[k.strip()] = v.strip().strip('"').strip("'")
    return values

_ENV_FILE_VALUES = _load_env(os.path.join(_INSTANCE_ROOT, ".env"))
for _k, _v in _ENV_FILE_VALUES.items():
    os.environ.setdefault(_k, _v)

# ── Test veritabani cozumlemesi ───────────────────────────────────
# Testler production casdb'ye ASLA baglanmamali. TEST_DB_URL verilmemisse
# instance'in DB_URL'inden turetilir (casdb / casdb_staging -> casdb_test).
#
# Ortamdaki DB_URL tek basina yeterli degil: tests/conftest.py, collection
# sirasinda cas_engine import edilirken KeyError olmasin diye DB_URL'e
# erisilemez bir sentinel koyuyor. Sentinel gercek bir DSN degil, ondan test
# veritabani turetilemez -- o yuzden turetme .env'deki degere duser. Ortamda
# gercek bir DB_URL varsa (operator export etmisse) o kazanir.
_SENTINEL_DB_URL = "postgresql://invalid:invalid@127.0.0.1:1/nodb"


def _is_usable_dsn(url):
    return bool(url) and url != _SENTINEL_DB_URL and "invalid" not in url


def _dbname_of(url):
    return url.rstrip("/").rsplit("/", 1)[-1].split("?")[0]


def _derive_test_db(url):
    """casdb / casdb_staging -> casdb_test.

    scripts/deploy.sh ayni eslemeyi yapiyor: iki instance de tek bir
    casdb_test kullanir, casdb_staging_test diye bir veritabani yok.
    Tanimadigimiz bir DB adinda tahmin yurutmeyiz -- TEST_DB_URL istenir.
    """
    if not _is_usable_dsn(url):
        return ""
    base = url.rstrip("/")
    head, _, tail = base.rpartition("/")
    name, sep, query = tail.partition("?")
    if name in ("casdb", "casdb_staging"):
        return head + "/casdb_test" + sep + query
    if name.endswith("_test"):
        return base
    return ""


_INSTANCE_DB_URL = os.environ.get("DB_URL", "")
if not _is_usable_dsn(_INSTANCE_DB_URL):
    _INSTANCE_DB_URL = _ENV_FILE_VALUES.get("DB_URL", "")

_REAL_DB_URL = os.environ.get("TEST_DB_URL", "") or _derive_test_db(_INSTANCE_DB_URL)

if not _is_usable_dsn(_REAL_DB_URL):
    pytest.exit(
        "Integration testleri icin bir test veritabani gerekli.\n"
        "TEST_DB_URL tanimlayin veya %s/.env icindeki DB_URL'in casdb ya da "
        "casdb_staging'e isaret ettiginden emin olun (casdb_test otomatik "
        "turetilir)." % _INSTANCE_ROOT,
        returncode=2)

# GUARD: production veritabanina yazmayi imkansiz kil.
_dbname = _dbname_of(_REAL_DB_URL)
if not _dbname.endswith("_test"):
    pytest.exit(
        "GUVENLIK DURDURMASI: integration testleri '%s' veritabanina baglanmak "
        "uzereydi. Test veritabani adi '_test' ile bitmelidir; production "
        "veritabanina test yazilmasi engellendi." % _dbname,
        returncode=2)


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
# KRITIK: DB_URL'i cas_engine import edilmeden ONCE degistir.
#
# cas_engine modul seviyesinde AUTH/WATCHLIST/ADMIN ornekleri kurar ve her
# biri __init__ icinde os.environ["DB_URL"] degerini kendi alanina kopyalar.
# Sonradan yapilan monkeypatch bu ornekleri etkilemez - yoneticiler production
# veritabanina yazmaya devam eder. Bu yuzden ortam degiskeni burada, import
# zincirinden once degistirilir; ayrica halihazirda kurulmus ornekler de
# yamalanır (import sirasi her zaman garanti degil).
# ──────────────────────────────────────────────────────
os.environ["DB_URL"] = _REAL_DB_URL


def _repoint_engine_managers(url):
    """cas_engine yoneticilerinin db_url alanini test veritabanina cevirir."""
    import cas_engine as _ce
    patched = []
    for name in ("AUTH", "WATCHLIST", "ADMIN", "DECISION", "TIER"):
        mgr = getattr(_ce, name, None)
        if mgr is not None and hasattr(mgr, "db_url"):
            mgr.db_url = url
            patched.append(name)
    return patched


@pytest.fixture(scope="session", autouse=True)
def _engine_uses_test_db():
    """Her oturumda yoneticilerin test veritabanina baktigini garantiler."""
    patched = _repoint_engine_managers(_REAL_DB_URL)
    if patched:
        print("[isolation] test veritabanina yonlendirildi: %s" % ", ".join(patched))
    yield


# ──────────────────────────────────────────────────────
# 0. seed_test_db: bos test veritabanina asgari referans veri
# ──────────────────────────────────────────────────────
@pytest.fixture(scope="session", autouse=True)
def seed_test_db():
    """Testlerin varligini varsaydigi asgari veriyi kurar.

    Test veritabani bos baslar. Bazi fixture'lar (existing_admin_id) ve
    butunluk testleri sistemde en az bir aktif admin bulundugunu varsayar;
    bu, production'un her zaman dogru olan bir ozelligi. Burada onu tekrar
    uretiyoruz - baska hicbir sey degil, cunku seed ne kadar buyurse
    testler o kadar production'un tesadufi durumuna baglanir.
    """
    conn = psycopg2.connect(_REAL_DB_URL)
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute("SELECT id FROM users WHERE role='admin' LIMIT 1")
    if not cur.fetchone():
        from cas_engine import AUTH
        cur.execute(
            """INSERT INTO users (email, password_hash, password_hash_type, name,
                                  role, tier, max_satellites, api_key,
                                  is_active, email_verified)
               VALUES (%s, %s, 'bcrypt', 'Seed Admin', 'admin', 'enterprise',
                       999, %s, true, true)""",
            ("seed-admin@cas.test",
             AUTH.hash_password_bcrypt("SeedAdminPass123!"),
             "seed-" + secrets.token_hex(16)))
        print("[seed] test admin olusturuldu: seed-admin@cas.test")
    cur.close()
    conn.close()
    yield


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
