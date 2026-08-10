"""
Integration tests: WatchlistManager (WATCHLIST global) + tier limit mantigi.

DIKKAT: Tier limit ENDPOINT seviyesinde (do_POST /watchlist/add) uygulaniyor,
        add_satellite() metodunda DEGIL. Bu yuzden:
          - add_satellite testleri: CRUD davranisi (upsert, regime, return)
          - tier limit testleri: TierConfig.get_limit + sayim mantigi (endpoint mantigini taklit)

add_satellite UPSERT yapar (ON CONFLICT DO UPDATE) - duplicate hata vermez, gunceller.
"""
import pytest
import psycopg2
import os
import secrets


def _db():
    return psycopg2.connect(os.environ["DB_URL"])


@pytest.fixture
def wl_test_user(admin_mgr, admin_id_fixture, db_committed):
    """Watchlist testleri icin temiz bir user (free tier, 0 uydu)."""
    email = "pytest-wl-" + secrets.token_hex(6) + "@cas.test"
    # starter (3 satellites) — free allows only 1, which cannot exercise
    # multi-satellite listing. Limit enforcement is covered separately.
    result, err = admin_mgr.create_user(admin_id_fixture, email, "TestPass123", "WL User", "operator", "starter")
    if err:
        pytest.fail(f"wl_test_user create basarisiz: {err}")
    db_committed.track(email)
    return result["user_id"]


class TestAddSatellite:
    def test_add_basic(self, watchlist_mgr, wl_test_user):
        result, err = watchlist_mgr.add_satellite(wl_test_user, "25544", "ISS (ZARYA)")
        assert err is None
        assert result["norad_id"] == "25544"
        assert result["sat_name"] == "ISS (ZARYA)"
        assert "id" in result

    def test_add_persists_to_db(self, watchlist_mgr, wl_test_user):
        watchlist_mgr.add_satellite(wl_test_user, "25544", "ISS")
        conn = _db()
        cur = conn.cursor()
        cur.execute("SELECT count(*) FROM watchlist WHERE user_id=%s AND norad_id=%s", (wl_test_user, "25544"))
        assert cur.fetchone()[0] == 1
        cur.close(); conn.close()

    def test_add_upsert_no_duplicate(self, watchlist_mgr, wl_test_user):
        """Ayni norad_id 2 kez eklenince UPSERT - tek kayit kalir."""
        watchlist_mgr.add_satellite(wl_test_user, "25544", "ISS")
        result2, err2 = watchlist_mgr.add_satellite(wl_test_user, "25544", "ISS UPDATED")
        assert err2 is None  # upsert, hata yok
        conn = _db()
        cur = conn.cursor()
        cur.execute("SELECT count(*), max(sat_name) FROM watchlist WHERE user_id=%s AND norad_id=%s", (wl_test_user, "25544"))
        row = cur.fetchone()
        cur.close(); conn.close()
        assert row[0] == 1  # tek kayit
        assert row[1] == "ISS UPDATED"  # guncellendi

    def test_add_sets_regime(self, watchlist_mgr, wl_test_user):
        """TLE ile eklenince regime hesaplanmali (leo/vleo/hybrid)."""
        watchlist_mgr.add_satellite(wl_test_user, "25544", "ISS")
        conn = _db()
        cur = conn.cursor()
        cur.execute("SELECT regime FROM watchlist WHERE user_id=%s AND norad_id=%s", (wl_test_user, "25544"))
        row = cur.fetchone()
        cur.close(); conn.close()
        assert row[0] in ("leo", "vleo", "hybrid")


class TestGetWatchlist:
    def test_get_empty(self, watchlist_mgr, wl_test_user):
        result = watchlist_mgr.get_watchlist(wl_test_user)
        assert isinstance(result, list)
        assert len(result) == 0

    def test_get_after_add(self, watchlist_mgr, wl_test_user):
        watchlist_mgr.add_satellite(wl_test_user, "25544", "ISS")
        watchlist_mgr.add_satellite(wl_test_user, "43013", "NOAA 20")
        result = watchlist_mgr.get_watchlist(wl_test_user)
        assert len(result) == 2
        norads = {r["norad_id"] for r in result}
        assert "25544" in norads
        assert "43013" in norads

    def test_get_structure(self, watchlist_mgr, wl_test_user):
        watchlist_mgr.add_satellite(wl_test_user, "25544", "ISS")
        result = watchlist_mgr.get_watchlist(wl_test_user)
        item = result[0]
        for key in ["id", "norad_id", "sat_name", "altitude_km", "regime"]:
            assert key in item


class TestRemoveSatellite:
    def test_remove_existing(self, watchlist_mgr, wl_test_user):
        watchlist_mgr.add_satellite(wl_test_user, "25544", "ISS")
        ok, err = watchlist_mgr.remove_satellite(wl_test_user, "25544")
        assert ok is True
        assert err is None
        # DB'de yok
        conn = _db()
        cur = conn.cursor()
        cur.execute("SELECT count(*) FROM watchlist WHERE user_id=%s AND norad_id=%s", (wl_test_user, "25544"))
        assert cur.fetchone()[0] == 0
        cur.close(); conn.close()

    def test_remove_nonexistent(self, watchlist_mgr, wl_test_user):
        ok, err = watchlist_mgr.remove_satellite(wl_test_user, "99999")
        assert ok is False
        assert err is not None
        assert "not found" in err.lower()


class TestTierLimits:
    """
    Tier limit mantigi. ENDPOINT seviyesinde uygulaniyor (do_POST),
    add_satellite metodunda degil. Burada TierConfig + sayim mantigini dogrularz.
    """

    def test_tier_config_limits(self):
        from cas_engine import TierConfig
        assert TierConfig.TIERS["free"]["max_satellites"] == 1
        assert TierConfig.TIERS["starter"]["max_satellites"] == 3
        assert TierConfig.TIERS["pro"]["max_satellites"] == 15
        assert TierConfig.TIERS["enterprise"]["max_satellites"] == 999

    def test_tier_get_limit(self):
        from cas_engine import TierConfig
        assert TierConfig.get_limit("free", "max_satellites") == 1
        assert TierConfig.get_limit("pro", "max_satellites") == 15

    def test_free_user_max_satellites_in_db(self, admin_mgr, admin_id_fixture, db_committed):
        """Free user'in DB'deki max_satellites = 1 olmali."""
        email = "pytest-wl-" + secrets.token_hex(6) + "@cas.test"
        u, _ = admin_mgr.create_user(admin_id_fixture, email, "TestPass123", "X", "operator", "free")
        db_committed.track(email)
        conn = _db()
        cur = conn.cursor()
        cur.execute("SELECT max_satellites FROM users WHERE id=%s", (u["user_id"],))
        assert cur.fetchone()[0] == 1
        cur.close(); conn.close()

    def test_tier_limit_enforcement_logic(self, watchlist_mgr, admin_mgr, admin_id_fixture, db_committed):
        """
        Endpoint mantigini taklit: free user (limit=1), 1 uydu ekli -> 2.'yi engellemeli.
        Endpoint kodu: if count >= max_sats: 403
        """
        email = "pytest-wl-" + secrets.token_hex(6) + "@cas.test"
        u, _ = admin_mgr.create_user(admin_id_fixture, email, "TestPass123", "X", "operator", "free")
        db_committed.track(email)
        uid = u["user_id"]
        # 1 uydu ekle (limit=1)
        watchlist_mgr.add_satellite(uid, "25544", "ISS")
        # Endpoint mantigi: count vs max_sats
        conn = _db()
        cur = conn.cursor()
        cur.execute("SELECT max_satellites FROM users WHERE id=%s", (uid,))
        max_sats = cur.fetchone()[0]
        cur.execute("SELECT count(*) FROM watchlist WHERE user_id=%s", (uid,))
        count = cur.fetchone()[0]
        cur.close(); conn.close()
        # 2. uyduyu eklemek endpoint'te engellenecekti
        assert count >= max_sats, f"count={count}, max={max_sats} - limit asilmali"

    def test_pro_user_allows_more(self, watchlist_mgr, admin_mgr, admin_id_fixture, db_committed):
        """Pro user (limit=15) birden fazla uydu ekleyebilmeli."""
        email = "pytest-wl-" + secrets.token_hex(6) + "@cas.test"
        u, _ = admin_mgr.create_user(admin_id_fixture, email, "TestPass123", "X", "operator", "pro")
        db_committed.track(email)
        uid = u["user_id"]
        for norad, name in [("25544", "ISS"), ("43013", "NOAA20"), ("48274", "STARLINK")]:
            watchlist_mgr.add_satellite(uid, norad, name)
        conn = _db()
        cur = conn.cursor()
        cur.execute("SELECT max_satellites FROM users WHERE id=%s", (uid,))
        max_sats = cur.fetchone()[0]
        cur.execute("SELECT count(*) FROM watchlist WHERE user_id=%s", (uid,))
        count = cur.fetchone()[0]
        cur.close(); conn.close()
        assert count == 3
        assert count < max_sats  # hala limit altinda
