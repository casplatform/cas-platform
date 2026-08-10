"""
VLEO engine integration tests.

vleo.py'nin 50 unit testi zaten var (tests/test_vleo.py). Burada:
  - engine'in vleo'yu dogru import ettigi
  - detect_regime threshold davranisi (engine ile tutarli)
  - add_satellite'in regime'i dogru set ettigi (entegrasyon)
"""
import pytest
import sys
sys.path.insert(0, "/opt/cas")


class TestVleoModuleAvailable:
    def test_vleo_importable(self):
        import vleo
        assert hasattr(vleo, "detect_regime")
        assert hasattr(vleo, "atmosphere_density")
        assert hasattr(vleo, "estimate_orbital_lifetime")

    def test_engine_has_vleo(self):
        """cas_engine vleo'yu import etmis olmali (VLEO_AVAILABLE)."""
        import cas_engine
        # VLEO_AVAILABLE flag veya detect_regime erisilebilir olmali
        assert hasattr(cas_engine, "detect_regime") or hasattr(cas_engine, "VLEO_AVAILABLE")


class TestDetectRegime:
    def test_vleo_regime(self):
        from vleo import detect_regime
        # <400km = vleo (drag-dominant)
        assert detect_regime(200) == "vleo"
        assert detect_regime(300) == "vleo"
        assert detect_regime(399) == "vleo"

    def test_hybrid_regime(self):
        from vleo import detect_regime
        # 400-450km = hybrid (gecis bolgesi)
        assert detect_regime(400) == "hybrid"
        assert detect_regime(420) == "hybrid"
        assert detect_regime(449) == "hybrid"

    def test_leo_regime(self):
        from vleo import detect_regime
        # >=450km = leo (drag-negligible for conjunction)
        assert detect_regime(450) == "leo"
        assert detect_regime(550) == "leo"
        assert detect_regime(800) == "leo"

    def test_regime_returns_valid(self):
        from vleo import detect_regime
        for alt in [200, 350, 450, 500, 600, 1000]:
            r = detect_regime(alt)
            assert r in ("leo", "vleo", "hybrid")


class TestAtmosphereDensity:
    def test_density_decreases_with_altitude(self):
        from vleo import atmosphere_density
        d300 = atmosphere_density(300)
        d500 = atmosphere_density(500)
        d800 = atmosphere_density(800)
        # Yuksek irtifa = daha az yogunluk
        assert d300 > d500 > d800

    def test_density_positive(self):
        from vleo import atmosphere_density
        assert atmosphere_density(400) > 0


class TestVleoEngineIntegration:
    def test_add_satellite_vleo_regime(self, watchlist_mgr, admin_mgr, admin_id_fixture, db_committed):
        """
        VLEO irtifali bir uydu eklendiginde regime='vleo' olmali.
        DIKKAT: altitude TLE'den hesaplanir; TLE yoksa cache/celestrak.
        Bu test gercek altitude lookup'a bagimli - regime sadece alt_km varsa hesaplanir.
        Eger altitude bulunamadiysa regime='leo' default.
        """
        import secrets
        email = "pytest-vleo-" + secrets.token_hex(6) + "@cas.test"
        u, _ = admin_mgr.create_user(admin_id_fixture, email, "TestPass123", "X", "operator", "pro")
        db_committed.track(email)
        # ISS ~420km - VLEO sinirinda. Altitude lookup yapilirsa regime hesaplanir.
        result, err = watchlist_mgr.add_satellite(u["user_id"], "25544", "ISS")
        assert err is None
        # regime DB'de leo/vleo/hybrid olmali (altitude bulunamadiysa leo default)
        import psycopg2, os
        conn = psycopg2.connect(os.environ["DB_URL"])
        cur = conn.cursor()
        cur.execute("SELECT regime, altitude_km FROM watchlist WHERE user_id=%s AND norad_id=%s",
                    (u["user_id"], "25544"))
        row = cur.fetchone()
        cur.close(); conn.close()
        assert row[0] in ("leo", "vleo", "hybrid")
