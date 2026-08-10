"""
Integration tests: Auth flow (AUTH.register / login / JWT / verify).

Strateji:
- Gercek DB'ye yazar, sonunda cleanup ile siler
- db_committed fixture: created_emails track + final DELETE
- pytest-*@cas.test pattern - production ile karismaz

Kapsam:
  - register: basarili, duplicate email, kisa sifre, gecersiz email
  - email verification token: olusturma, kullanma, expire
  - login: basarili, yanlis sifre, verified olmayan kullanici, deactivated
  - JWT: olusturma, validate, expire kontrolu
  - login_log: kayit yazilmasi
  - user_activity: login eyleminin loglanmasi
"""
import pytest
import psycopg2
import os
import time


class TestRegister:
    """AUTH.register() flow testleri."""

    def test_register_success(self, auth_mgr, db_committed, test_email):
        result, err = auth_mgr.register(test_email, "ValidPass123", "Test User")
        db_committed.track(test_email)
        assert err is None
        assert result is not None
        assert result.get("email") == test_email
        assert result.get("api_key", "").startswith("cas_")
        assert len(result.get("api_key", "")) > 20

    def test_register_duplicate_email(self, auth_mgr, db_committed, test_email):
        # ilk register
        auth_mgr.register(test_email, "ValidPass123", "First")
        db_committed.track(test_email)
        # ikinci - ayni email ile
        result, err = auth_mgr.register(test_email, "AnotherPass456", "Second")
        assert err is not None
        assert result is None
        # mesaj icerigi: zaten varolan / duplicate / unique constraint
        assert any(t in err.lower() for t in ["zaten", "exist", "duplicate", "unique"])

    def test_register_short_password(self, auth_mgr, test_email):
        result, err = auth_mgr.register(test_email, "abc", "Test")
        assert err is not None
        assert result is None
        assert "6" in err or "karakter" in err.lower() or "short" in err.lower()

    def test_register_empty_email(self, auth_mgr):
        result, err = auth_mgr.register("", "ValidPass123", "Test")
        assert err is not None
        assert result is None

    def test_register_empty_password(self, auth_mgr, test_email):
        result, err = auth_mgr.register(test_email, "", "Test")
        assert err is not None
        assert result is None

    def test_register_default_tier_is_free(self, auth_mgr, db_committed, test_email):
        auth_mgr.register(test_email, "ValidPass123", "Test")
        db_committed.track(test_email)
        # DB'den tier kontrol
        conn = psycopg2.connect(os.environ["DB_URL"])
        cur = conn.cursor()
        cur.execute("SELECT tier, role FROM users WHERE email=%s", (test_email,))
        row = cur.fetchone()
        cur.close()
        conn.close()
        assert row is not None
        assert row[0] == "free"
        assert row[1] == "operator"

    def test_register_uses_bcrypt(self, auth_mgr, db_committed, test_email):
        """Yeni kullanicilar bcrypt hash kullanmali (sha256 degil)."""
        auth_mgr.register(test_email, "ValidPass123", "Test")
        db_committed.track(test_email)
        conn = psycopg2.connect(os.environ["DB_URL"])
        cur = conn.cursor()
        cur.execute("SELECT password_hash, password_hash_type FROM users WHERE email=%s", (test_email,))
        row = cur.fetchone()
        cur.close()
        conn.close()
        assert row is not None
        # bcrypt hash $2b$ veya $2a$ ile baslar
        assert row[0].startswith("$2"), f"Hash bcrypt degil: {row[0][:10]}"


class TestLogin:
    """AUTH.login() flow testleri."""

    def test_login_success(self, auth_mgr, created_test_user):
        result, err = auth_mgr.login(
            created_test_user["email"],
            created_test_user["password"]
        )
        assert err is None
        assert result is not None
        assert "token" in result
        assert result.get("email") == created_test_user["email"]

    def test_login_wrong_password(self, auth_mgr, created_test_user):
        result, err = auth_mgr.login(
            created_test_user["email"],
            "WrongPassword999"
        )
        assert err is not None
        assert result is None

    def test_login_nonexistent_email(self, auth_mgr):
        result, err = auth_mgr.login("noone-pytest@cas.test", "AnyPass123")
        assert err is not None
        assert result is None

    def test_login_unverified_email_blocked(self, auth_mgr, db_committed, test_email):
        """email_verified=false olan user login olamamali."""
        auth_mgr.register(test_email, "ValidPass123", "Unverified")
        db_committed.track(test_email)
        # login() filters on is_active; activate the account but deliberately
        # leave email_verified=false so the verification gate is what we test.
        _c = psycopg2.connect(os.environ["DB_URL"]); _c.autocommit = True
        _cur = _c.cursor()
        _cur.execute("UPDATE users SET is_active=true, email_verified=false WHERE email=%s", (test_email,))
        _cur.close(); _c.close()
        # NOT: created_test_user'da verified=true yapiyoruz, burada YAPMAYIZ
        result, err = auth_mgr.login(test_email, "ValidPass123")
        # 2 senaryo olabilir: error ile blok veya success ama "verification_required" flag
        if err is None:
            # success dondu - flag kontrol
            assert result.get("email_verified") is False or result.get("verification_required") is True
        else:
            # error - mesaj verification ile ilgili olmali
            assert any(t in err.lower() for t in ["verif", "dogrulan", "confirm"])

    def test_login_deactivated_user_blocked(self, auth_mgr, created_test_user):
        """is_active=false yapilan user login olamamali."""
        conn = psycopg2.connect(os.environ["DB_URL"])
        conn.autocommit = True
        cur = conn.cursor()
        cur.execute("UPDATE users SET is_active=false WHERE id=%s", (created_test_user["id"],))
        cur.close()
        conn.close()
        
        result, err = auth_mgr.login(
            created_test_user["email"],
            created_test_user["password"]
        )
        assert err is not None or (result and result.get("active") is False)

    def test_login_writes_login_log(self, auth_mgr, created_test_user):
        """Basarili login -> login_log tablosuna kayit."""
        # Once mevcut sayim
        conn = psycopg2.connect(os.environ["DB_URL"])
        cur = conn.cursor()
        cur.execute(
            "SELECT count(*) FROM login_log WHERE user_id=%s AND success=true",
            (created_test_user["id"],)
        )
        before = cur.fetchone()[0]
        cur.close()
        conn.close()
        
        # Login at
        auth_mgr.login(created_test_user["email"], created_test_user["password"])
        time.sleep(0.2)  # async log icin
        
        conn = psycopg2.connect(os.environ["DB_URL"])
        cur = conn.cursor()
        cur.execute(
            "SELECT count(*) FROM login_log WHERE user_id=%s AND success=true",
            (created_test_user["id"],)
        )
        after = cur.fetchone()[0]
        cur.close()
        conn.close()
        
        assert after > before, f"login_log artmadi: {before} -> {after}"

    def test_login_failed_attempt_logged(self, auth_mgr, created_test_user):
        """Basarisiz login -> login_log success=false."""
        conn = psycopg2.connect(os.environ["DB_URL"])
        cur = conn.cursor()
        cur.execute(
            "SELECT count(*) FROM login_log WHERE email=%s AND success=false",
            (created_test_user["email"],)
        )
        before = cur.fetchone()[0]
        cur.close()
        conn.close()
        
        auth_mgr.login(created_test_user["email"], "DefinitelyWrongPass")
        time.sleep(0.2)
        
        conn = psycopg2.connect(os.environ["DB_URL"])
        cur = conn.cursor()
        cur.execute(
            "SELECT count(*) FROM login_log WHERE email=%s AND success=false",
            (created_test_user["email"],)
        )
        after = cur.fetchone()[0]
        cur.close()
        conn.close()
        
        assert after > before


class TestJWT:
    """JWT olusturma/validate testleri."""

    def test_login_returns_valid_jwt(self, auth_mgr, created_test_user):
        result, err = auth_mgr.login(
            created_test_user["email"],
            created_test_user["password"]
        )
        assert err is None
        token = result.get("token")
        assert token is not None
        # JWT format: header.payload.signature (3 segment)
        parts = token.split(".")
        assert len(parts) == 3, f"JWT formati gecersiz: {len(parts)} segment"

    def test_jwt_decode_returns_uid(self, auth_mgr, created_test_user):
        """JWT decode edilince payload uid icermeli."""
        result, err = auth_mgr.login(
            created_test_user["email"],
            created_test_user["password"]
        )
        assert err is None
        token = result["token"]
        
        # AUTH.decode_jwt veya benzeri metod yoksa import et:
        import jwt as pyjwt
        secret = os.environ.get("AUTH_SECRET") or os.environ.get("JWT_SECRET")
        if not secret:
            pytest.skip("AUTH_SECRET/JWT_SECRET .env'de yok")
        payload = pyjwt.decode(token, secret, algorithms=["HS256"])
        assert payload.get("uid") == created_test_user["id"]
        assert payload.get("email") == created_test_user["email"]


class TestUserActivity:
    """user_activity log testleri (login + sat_add + api_access)."""

    def test_login_logged_to_user_activity(self, auth_mgr, created_test_user):
        """Basarili login -> user_activity 'login' kaydi."""
        conn = psycopg2.connect(os.environ["DB_URL"])
        cur = conn.cursor()
        cur.execute(
            "SELECT count(*) FROM user_activity WHERE user_id=%s AND action='login'",
            (created_test_user["id"],)
        )
        before = cur.fetchone()[0]
        cur.close()
        conn.close()
        
        auth_mgr.login(created_test_user["email"], created_test_user["password"])
        time.sleep(0.2)
        
        conn = psycopg2.connect(os.environ["DB_URL"])
        cur = conn.cursor()
        cur.execute(
            "SELECT count(*) FROM user_activity WHERE user_id=%s AND action='login'",
            (created_test_user["id"],)
        )
        after = cur.fetchone()[0]
        cur.close()
        conn.close()
        
        assert after > before
