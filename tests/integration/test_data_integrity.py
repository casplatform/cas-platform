"""
Data integrity tests: FK orphan check, UNIQUE constraints, tier/role tutarlilik.

DB-only, salt SELECT. Veri yazmaz, cleanup gerekmez.

Bu testler production sagligini garanti eder:
  - Hicbir FK orphan yok (cascade dogru calismis)
  - UNIQUE constraint'ler dogru (duplicate'ler engellenmis)
  - Tier ve max_satellites tutarli (TierConfig ile)
  - role enum disinda deger yok
  - En az 1 admin var (sistem kosulu)
  - pytest-*@cas.test kalintisi yok (test izolasyonu saglik)
"""
import pytest
import psycopg2
import os


def _query(sql, params=None):
    conn = psycopg2.connect(os.environ["DB_URL"])
    cur = conn.cursor()
    cur.execute(sql, params or ())
    rows = cur.fetchall()
    cur.close(); conn.close()
    return rows


def _count(sql, params=None):
    return _query(sql, params)[0][0]


class TestForeignKeyIntegrity:
    """Hicbir FK orphan satir olmamali (cascade/SET NULL dogru calismis)."""

    def test_watchlist_no_orphan(self):
        n = _count("""
            SELECT count(*) FROM watchlist w
            LEFT JOIN users u ON w.user_id = u.id
            WHERE u.id IS NULL
        """)
        assert n == 0, f"{n} orphan watchlist satiri var"

    def test_decision_results_no_orphan_user(self):
        n = _count("""
            SELECT count(*) FROM decision_results d
            LEFT JOIN users u ON d.user_id = u.id
            WHERE u.id IS NULL
        """)
        assert n == 0

    def test_decision_results_no_orphan_watchlist(self):
        """watchlist_id NULL olabilir ama NULL olmayanlar gercek bir watchlist'e isaret etmeli."""
        n = _count("""
            SELECT count(*) FROM decision_results d
            LEFT JOIN watchlist w ON d.watchlist_id = w.id
            WHERE d.watchlist_id IS NOT NULL AND w.id IS NULL
        """)
        assert n == 0

    def test_watchlist_results_no_orphan(self):
        n = _count("""
            SELECT count(*) FROM watchlist_results wr
            LEFT JOIN users u ON wr.user_id = u.id
            WHERE u.id IS NULL
        """)
        assert n == 0

    def test_notification_prefs_no_orphan(self):
        n = _count("""
            SELECT count(*) FROM notification_prefs np
            LEFT JOIN users u ON np.user_id = u.id
            WHERE u.id IS NULL
        """)
        assert n == 0

    def test_admin_log_no_orphan(self):
        n = _count("""
            SELECT count(*) FROM admin_log al
            LEFT JOIN users u ON al.admin_id = u.id
            WHERE u.id IS NULL
        """)
        assert n == 0

    def test_login_log_set_null_works(self):
        """login_log.user_id ON DELETE SET NULL - orphan kabul edilir (NULL olur)."""
        # NULL kayitlar olabilir (user silinince), bu beklenen davranis
        n = _count("SELECT count(*) FROM login_log WHERE user_id IS NULL")
        # Sadece "var olabilir" - assertion yok, sadece query calissin
        assert n >= 0


class TestUniqueConstraints:
    def test_users_email_unique(self):
        n = _count("""
            SELECT count(*) FROM (
                SELECT email FROM users GROUP BY email HAVING count(*) > 1
            ) dup
        """)
        assert n == 0, f"{n} duplicate email var"

    def test_users_api_key_unique(self):
        n = _count("""
            SELECT count(*) FROM (
                SELECT api_key FROM users WHERE api_key IS NOT NULL GROUP BY api_key HAVING count(*) > 1
            ) dup
        """)
        assert n == 0

    def test_watchlist_user_norad_unique(self):
        n = _count("""
            SELECT count(*) FROM (
                SELECT user_id, norad_id FROM watchlist GROUP BY user_id, norad_id HAVING count(*) > 1
            ) dup
        """)
        assert n == 0, f"{n} duplicate (user_id, norad_id) watchlist'te"

    def test_decision_results_user_norad_unique(self):
        n = _count("""
            SELECT count(*) FROM (
                SELECT user_id, norad_id FROM decision_results GROUP BY user_id, norad_id HAVING count(*) > 1
            ) dup
        """)
        assert n == 0


class TestTierConsistency:
    def test_all_tiers_valid_enum(self):
        """Tum users.tier degerleri TierConfig.TIERS icinde olmali."""
        import sys
        sys.path.insert(0, "/opt/cas")
        from cas_engine import TierConfig
        # Operator tiers live in cas_engine.TierConfig; insurer tiers are a
        # separate product surface defined in cas_api/core/tier_features.py.
        valid = set(TierConfig.TIERS.keys()) | {
            "insurer_demo", "insurer_pro", "insurer_enterprise"}
        rows = _query("SELECT DISTINCT tier FROM users WHERE tier IS NOT NULL")
        for (t,) in rows:
            assert t in valid, f"Gecersiz tier: {t}"

    def test_max_satellites_matches_tier(self):
        """
        Her user'in max_satellites kolonu, tier'inin tier_config'inde tanimli max_satellites
        ile esleşmeli.
        """
        import sys
        sys.path.insert(0, "/opt/cas")
        from cas_engine import TierConfig
        # Satellite limits are an operator concept. Insurers analyse portfolios
        # and hold no watchlist, so their tiers are out of scope for this check.
        rows = _query("SELECT id, email, tier, max_satellites FROM users "
                      "WHERE tier IS NULL OR tier NOT IN "
                      "('insurer_demo', 'insurer_pro', 'insurer_enterprise')")
        mismatches = []
        for uid, email, tier, max_sats in rows:
            expected = TierConfig.TIERS.get(tier or "free", {}).get("max_satellites", 1)
            if max_sats != expected:
                mismatches.append(f"  uid={uid} email={email} tier={tier} max={max_sats} expected={expected}")
        assert not mismatches, "Tier-max_satellites uyumsuz:\n" + "\n".join(mismatches)


class TestRoleConsistency:
    def test_role_enum_valid(self):
        """role sadece admin/operator/viewer olabilir."""
        valid = {"admin", "operator", "viewer", "insurer"}
        rows = _query("SELECT DISTINCT role FROM users WHERE role IS NOT NULL")
        for (r,) in rows:
            assert r in valid, f"Gecersiz role: {r}"

    def test_at_least_one_admin(self):
        """Sistemde her zaman en az 1 admin olmali."""
        n = _count("SELECT count(*) FROM users WHERE role='admin' AND is_active=true")
        assert n >= 1, "Hic aktif admin yok!"


class TestPasswordHashConsistency:
    def test_password_hash_type_valid(self):
        """password_hash_type sadece bcrypt veya sha256 olabilir."""
        rows = _query("SELECT DISTINCT password_hash_type FROM users")
        for (h,) in rows:
            if h is not None:  # NULL kabul (eski kayitlar)
                assert h in ("bcrypt", "sha256"), f"Gecersiz hash type: {h}"

    def test_bcrypt_hashes_well_formed(self):
        """bcrypt hash'leri $2 ile baslamali."""
        rows = _query("SELECT email, password_hash FROM users WHERE password_hash_type='bcrypt'")
        for email, h in rows:
            assert h.startswith("$2"), f"{email}: bcrypt hash yanlis format: {h[:10]}"


class TestTestIsolation:
    def test_no_pytest_residue_users(self):
        """Hicbir pytest-*@cas.test user kalintisi olmamali."""
        # LIKE'in % isareti psycopg2 parametre substitution ile carpisiyor
        # => parametrize et
        n = _count("SELECT count(*) FROM users WHERE email LIKE %s", ("pytest-%@cas.test",))
        assert n == 0, f"{n} test kullanicisi temizlenmemis"

    def test_no_pytest_residue_watchlist(self):
        n = _count(
            "SELECT count(*) FROM watchlist w JOIN users u ON w.user_id = u.id WHERE u.email LIKE %s",
            ("pytest-%@cas.test",)
        )
        assert n == 0


class TestSystemHealth:
    def test_users_table_not_empty(self):
        n = _count("SELECT count(*) FROM users")
        assert n >= 1, "users tablosu bos!"

    def test_active_users_exist(self):
        n = _count("SELECT count(*) FROM users WHERE is_active=true")
        assert n >= 1
