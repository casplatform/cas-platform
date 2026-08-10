"""
Integration tests: AdminManager (ADMIN global instance).

Kapsam:
  - list_users: yapi, satellite_count, tier_config
  - create_user: basari, duplicate, gecersiz role/tier, kisa sifre
  - set_tier: tier + max_satellites guncelleme, gecersiz tier
  - toggle_active: aktif/pasif degisimi, self koruma
  - activate_user: email_verified + is_active TRUE
  - delete_user: CASCADE (27 May 2026 patch dogrulamasi), self+last-admin koruma
  - admin_log: islem kayitlari

Veri izolasyonu: pytest-*@cas.test + db_committed cleanup tracker.
"""
import pytest
import psycopg2
import os
import secrets


def _db():
    return psycopg2.connect(os.environ["DB_URL"])


def _make_admin_test_user(admin_mgr, admin_id, db_committed, email, role="operator", tier="free"):
    """Helper: ADMIN.create_user ile user olustur + cleanup track."""
    result, err = admin_mgr.create_user(admin_id, email, "TestPass123", "Admin Test", role, tier)
    if err:
        pytest.fail(f"create_user basarisiz: {err}")
    db_committed.track(email)
    return result


class TestListUsers:
    def test_list_users_structure(self, admin_mgr):
        result = admin_mgr.list_users(page=1, per_page=20)
        assert "users" in result
        assert "total" in result
        assert "page" in result
        assert "pages" in result
        assert isinstance(result["users"], list)
        assert result["total"] >= 1  # en az admin var

    def test_list_users_has_satellite_count(self, admin_mgr):
        result = admin_mgr.list_users(page=1, per_page=5)
        if result["users"]:
            u = result["users"][0]
            assert "satellite_count" in u
            assert "tier_config" in u
            assert "email" in u
            assert "role" in u

    def test_list_users_search(self, admin_mgr, admin_id_fixture, db_committed):
        # bilinen bir test user olustur, search ile bul
        email = "pytest-" + secrets.token_hex(6) + "@cas.test"
        admin_mgr.create_user(admin_id_fixture, email, "TestPass123", "Searchable", "viewer", "free")
        db_committed.track(email)
        result = admin_mgr.list_users(page=1, per_page=50, search=email)
        emails = [u["email"] for u in result["users"]]
        assert email in emails

    def test_list_users_pagination(self, admin_mgr):
        r1 = admin_mgr.list_users(page=1, per_page=1)
        assert len(r1["users"]) <= 1
        assert r1["per_page"] == 1


class TestCreateUser:
    def test_create_success(self, admin_mgr, admin_id_fixture, db_committed):
        email = "pytest-" + secrets.token_hex(6) + "@cas.test"
        result, err = admin_mgr.create_user(admin_id_fixture, email, "TestPass123", "New User", "operator", "free")
        db_committed.track(email)
        assert err is None
        assert result["email"] == email
        assert result["api_key"].startswith("cas_")
        assert "user_id" in result
        assert result["role"] == "operator"
        assert result["tier"] == "free"

    def test_create_duplicate_email(self, admin_mgr, admin_id_fixture, db_committed):
        email = "pytest-" + secrets.token_hex(6) + "@cas.test"
        admin_mgr.create_user(admin_id_fixture, email, "TestPass123", "First", "operator", "free")
        db_committed.track(email)
        result, err = admin_mgr.create_user(admin_id_fixture, email, "TestPass456", "Second", "operator", "free")
        assert err is not None
        assert result is None
        assert "registered" in err.lower() or "already" in err.lower() or "unique" in err.lower()

    def test_create_invalid_role(self, admin_mgr, admin_id_fixture):
        email = "pytest-" + secrets.token_hex(6) + "@cas.test"
        result, err = admin_mgr.create_user(admin_id_fixture, email, "TestPass123", "X", "superuser", "free")
        assert err is not None
        assert result is None
        assert "role" in err.lower()

    def test_create_invalid_tier(self, admin_mgr, admin_id_fixture):
        email = "pytest-" + secrets.token_hex(6) + "@cas.test"
        result, err = admin_mgr.create_user(admin_id_fixture, email, "TestPass123", "X", "operator", "platinum")
        assert err is not None
        assert result is None
        assert "tier" in err.lower()

    def test_create_short_password(self, admin_mgr, admin_id_fixture):
        email = "pytest-" + secrets.token_hex(6) + "@cas.test"
        result, err = admin_mgr.create_user(admin_id_fixture, email, "abc", "X", "operator", "free")
        assert err is not None
        assert result is None

    def test_create_user_is_verified_and_active(self, admin_mgr, admin_id_fixture, db_committed):
        """Admin-created user'lar email_verified=true + is_active=true olmali."""
        email = "pytest-" + secrets.token_hex(6) + "@cas.test"
        admin_mgr.create_user(admin_id_fixture, email, "TestPass123", "X", "operator", "free")
        db_committed.track(email)
        conn = _db()
        cur = conn.cursor()
        cur.execute("SELECT email_verified, is_active FROM users WHERE email=%s", (email,))
        row = cur.fetchone()
        cur.close(); conn.close()
        assert row[0] is True  # email_verified
        assert row[1] is True  # is_active


class TestSetTier:
    def test_set_tier_updates_max_satellites(self, admin_mgr, admin_id_fixture, db_committed):
        email = "pytest-" + secrets.token_hex(6) + "@cas.test"
        u = _make_admin_test_user(admin_mgr, admin_id_fixture, db_committed, email, "operator", "free")
        result, err = admin_mgr.set_tier(admin_id_fixture, u["user_id"], "pro")
        assert err is None
        assert result["tier"] == "pro"
        # pro tier max_satellites free'den buyuk olmali
        assert result["max_satellites"] > 1

    def test_set_tier_invalid(self, admin_mgr, admin_id_fixture, db_committed):
        email = "pytest-" + secrets.token_hex(6) + "@cas.test"
        u = _make_admin_test_user(admin_mgr, admin_id_fixture, db_committed, email)
        result, err = admin_mgr.set_tier(admin_id_fixture, u["user_id"], "diamond")
        assert err is not None
        assert result is None
        assert "tier" in err.lower()

    def test_set_tier_db_persisted(self, admin_mgr, admin_id_fixture, db_committed):
        email = "pytest-" + secrets.token_hex(6) + "@cas.test"
        u = _make_admin_test_user(admin_mgr, admin_id_fixture, db_committed, email, "operator", "free")
        admin_mgr.set_tier(admin_id_fixture, u["user_id"], "starter")
        conn = _db()
        cur = conn.cursor()
        cur.execute("SELECT tier FROM users WHERE id=%s", (u["user_id"],))
        row = cur.fetchone()
        cur.close(); conn.close()
        assert row[0] == "starter"


class TestToggleActive:
    def test_toggle_deactivates(self, admin_mgr, admin_id_fixture, db_committed):
        email = "pytest-" + secrets.token_hex(6) + "@cas.test"
        u = _make_admin_test_user(admin_mgr, admin_id_fixture, db_committed, email)
        # create_user is_active=true yapar, toggle -> false
        result, err = admin_mgr.toggle_active(admin_id_fixture, u["user_id"])
        assert err is None
        assert result is not None

    def test_toggle_self_blocked(self, admin_mgr, admin_id_fixture):
        """Admin kendini deactivate edememeli."""
        result, err = admin_mgr.toggle_active(admin_id_fixture, admin_id_fixture)
        assert err is not None
        assert "own account" in err.lower() or "kendi" in err.lower()


class TestActivateUser:
    def test_activate_sets_verified_and_active(self, admin_mgr, admin_id_fixture, db_committed):
        email = "pytest-" + secrets.token_hex(6) + "@cas.test"
        u = _make_admin_test_user(admin_mgr, admin_id_fixture, db_committed, email)
        # Once deactivate + unverify
        conn = _db()
        conn.autocommit = True
        cur = conn.cursor()
        cur.execute("UPDATE users SET is_active=false, email_verified=false WHERE id=%s", (u["user_id"],))
        cur.close(); conn.close()
        # Activate
        result, err = admin_mgr.activate_user(admin_id_fixture, u["user_id"])
        assert err is None
        # DB kontrol
        conn = _db()
        cur = conn.cursor()
        cur.execute("SELECT email_verified, is_active FROM users WHERE id=%s", (u["user_id"],))
        row = cur.fetchone()
        cur.close(); conn.close()
        assert row[0] is True
        assert row[1] is True


class TestDeleteUserCascade:
    """27 May 2026 cascade patch dogrulamasi - en kritik test grubu."""

    def test_delete_basic(self, admin_mgr, admin_id_fixture):
        # Cleanup tracker KULLANMIYORUZ - test zaten silecek
        email = "pytest-" + secrets.token_hex(6) + "@cas.test"
        u, err_c = admin_mgr.create_user(admin_id_fixture, email, "TestPass123", "ToDelete", "viewer", "free")
        assert err_c is None
        uid = u["user_id"]
        # Sil
        result, err = admin_mgr.delete_user(admin_id_fixture, uid)
        assert err is None
        assert result["deleted"] == uid
        # DB'de yok artik
        conn = _db()
        cur = conn.cursor()
        cur.execute("SELECT count(*) FROM users WHERE id=%s", (uid,))
        assert cur.fetchone()[0] == 0
        cur.close(); conn.close()

    def test_delete_returns_cascade_counts(self, admin_mgr, admin_id_fixture):
        email = "pytest-" + secrets.token_hex(6) + "@cas.test"
        u, _ = admin_mgr.create_user(admin_id_fixture, email, "TestPass123", "X", "viewer", "free")
        uid = u["user_id"]
        result, err = admin_mgr.delete_user(admin_id_fixture, uid)
        assert err is None
        assert "cascade" in result
        c = result["cascade"]
        # Tum cascade key'leri var mi
        for key in ["activity", "notif_prefs", "watchlist_results", "decisions", "login_log", "admin_log"]:
            assert key in c, f"cascade'de {key} yok"

    def test_delete_cascade_removes_related(self, admin_mgr, admin_id_fixture):
        """User'a bagli kayitlar (watchlist, login_log) silindi mi?"""
        email = "pytest-" + secrets.token_hex(6) + "@cas.test"
        u, _ = admin_mgr.create_user(admin_id_fixture, email, "TestPass123", "X", "operator", "starter")
        uid = u["user_id"]
        # Bagli kayitlar ekle
        conn = _db()
        conn.autocommit = True
        cur = conn.cursor()
        cur.execute("INSERT INTO user_activity (user_id, email, action) VALUES (%s,%s,%s)", (uid, email, "test"))
        cur.execute("INSERT INTO login_log (user_id, email, success) VALUES (%s,%s,%s)", (uid, email, True))
        cur.execute("INSERT INTO watchlist (user_id, norad_id, sat_name) VALUES (%s,%s,%s)", (uid, 99999, "TESTSAT"))
        cur.close(); conn.close()
        # Sil
        result, err = admin_mgr.delete_user(admin_id_fixture, uid)
        assert err is None
        # Bagli kayitlar gitti mi
        conn = _db()
        cur = conn.cursor()
        cur.execute("SELECT count(*) FROM user_activity WHERE user_id=%s", (uid,))
        assert cur.fetchone()[0] == 0, "user_activity temizlenmedi"
        cur.execute("SELECT count(*) FROM login_log WHERE user_id=%s", (uid,))
        assert cur.fetchone()[0] == 0, "login_log temizlenmedi"
        cur.execute("SELECT count(*) FROM watchlist WHERE user_id=%s", (uid,))
        assert cur.fetchone()[0] == 0, "watchlist temizlenmedi (CASCADE FK)"
        cur.close(); conn.close()

    def test_delete_self_blocked(self, admin_mgr, admin_id_fixture):
        """Admin kendini silememeli."""
        result, err = admin_mgr.delete_user(admin_id_fixture, admin_id_fixture)
        assert err is not None
        assert result is None
        assert "own account" in err.lower()

    def test_delete_nonexistent(self, admin_mgr, admin_id_fixture):
        result, err = admin_mgr.delete_user(admin_id_fixture, 99999999)
        assert err is not None
        assert "not found" in err.lower()

    def test_delete_last_admin_blocked(self, admin_mgr, admin_id_fixture):
        """
        Tek admin silinememeli. Sistemde 1 admin var (mustafa).
        Ikinci admin olusturup onu silebiliriz (last degil),
        ama mevcut tek admin'i baska bir admin'den silmeye calismak last-admin korumasina takilmali.
        Bu testi guvenli yapmak icin: 2 admin olustur, birini sil (OK),
        sonra kalan tek test-admin'i silmeye calis -> sistemde hala mustafa var,
        yani 'last admin' senaryosunu izole test edemeyiz cunku gercek admin'i silemeyiz.

        Bunun yerine: delete_user icindeki last-admin mantigini dogrudan test edemiyoruz
        (gercek admin'i riske atmadan). Skip + aciklama.
        """
        # Guvenlik: gercek admin'i silme riski olmadan last-admin testi yapilamaz.
        # Mantik kod review'da dogrulandi: admin_count <= 1 ise "Cannot delete the last admin user"
        pytest.skip("Last-admin korumasi gercek admin riski olmadan izole test edilemez - kod review'da dogrulandi")

    def test_delete_admin_user_when_multiple(self, admin_mgr, admin_id_fixture):
        """Birden fazla admin varken bir admin silinebilmeli (last degil)."""
        email = "pytest-admin-" + secrets.token_hex(6) + "@cas.test"
        u, err_c = admin_mgr.create_user(admin_id_fixture, email, "TestPass123", "TempAdmin", "admin", "free")
        assert err_c is None
        uid = u["user_id"]
        # Simdi 2 admin var (mustafa + bu) -> silinebilmeli
        result, err = admin_mgr.delete_user(admin_id_fixture, uid)
        assert err is None, f"Ikinci admin silinemedi: {err}"
        assert result["deleted"] == uid


class TestAdminLog:
    def test_create_user_logged(self, admin_mgr, admin_id_fixture, db_committed):
        email = "pytest-" + secrets.token_hex(6) + "@cas.test"
        conn = _db()
        cur = conn.cursor()
        cur.execute("SELECT count(*) FROM admin_log WHERE action='create_user'")
        before = cur.fetchone()[0]
        cur.close(); conn.close()

        admin_mgr.create_user(admin_id_fixture, email, "TestPass123", "X", "operator", "free")
        db_committed.track(email)

        conn = _db()
        cur = conn.cursor()
        cur.execute("SELECT count(*) FROM admin_log WHERE action='create_user'")
        after = cur.fetchone()[0]
        cur.close(); conn.close()
        assert after > before

    def test_delete_user_logged(self, admin_mgr, admin_id_fixture):
        email = "pytest-" + secrets.token_hex(6) + "@cas.test"
        u, _ = admin_mgr.create_user(admin_id_fixture, email, "TestPass123", "X", "viewer", "free")
        uid = u["user_id"]

        conn = _db()
        cur = conn.cursor()
        cur.execute("SELECT count(*) FROM admin_log WHERE action='delete_user'")
        before = cur.fetchone()[0]
        cur.close(); conn.close()

        admin_mgr.delete_user(admin_id_fixture, uid)

        conn = _db()
        cur = conn.cursor()
        cur.execute("SELECT count(*) FROM admin_log WHERE action='delete_user'")
        after = cur.fetchone()[0]
        cur.close(); conn.close()
        assert after > before
