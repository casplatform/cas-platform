"""Unit tests for rank_debris.compute_rankings — pure function core."""
import sys, os
sys.path.insert(0, "/opt/cas")
from rank_debris import is_debris, classify_band, compute_rankings


class TestIsDebris:
    def test_classic_debris(self):
        assert is_debris("COSMOS 1408 DEB") is True
        assert is_debris("FENGYUN 1C DEBRIS") is True

    def test_rocket_body(self):
        assert is_debris("ARIANE 42P R/B") is True

    def test_active_satellite_not_debris(self):
        assert is_debris("STARLINK-12345") is False
        assert is_debris("ISS (ZARYA)") is False

    def test_empty_name(self):
        assert is_debris("") is False
        assert is_debris(None) is False


class TestClassifyBand:
    def test_low_band(self):
        assert classify_band(550) == "low"
        assert classify_band(500) == "low"
        assert classify_band(600) == "low"

    def test_mid_band(self):
        assert classify_band(1100) == "mid"
        assert classify_band(1000) == "mid"
        assert classify_band(1200) == "mid"

    def test_out_of_band(self):
        assert classify_band(400) is None
        assert classify_band(800) is None
        assert classify_band(1500) is None
        assert classify_band(None) is None


class TestComputeRankings:
    def _cdm(self, cid, s1, n1, s2, n2, pc):
        return {"cdm_id": cid, "sat1": s1, "norad1": n1,
                "sat2": s2, "norad2": n2, "pc": pc, "fetched_at": "2026-04-14"}

    def test_empty_input(self):
        r = compute_rankings([], {})
        assert r == {"all": [], "low": [], "mid": []}

    def test_single_debris_counted_once(self):
        cdms = [self._cdm("C1", "STARLINK-1", "100", "COSMOS DEB", "200", 1e-4)]
        r = compute_rankings(cdms, {"200": 550})
        assert len(r["all"]) == 1
        assert r["all"][0]["norad_id"] == "200"
        assert r["all"][0]["cdm_count"] == 1
        assert r["all"][0]["unique_counterparties"] == 1

    def test_threat_score_ranking(self):
        # Debris A has 3 counterparties with low Pc; debris B has 1 with very high Pc
        # threat_score = counterparties*1000 + cumulative_pc*1e6
        # A: 3*1000 + 3e-5*1e6 = 3030, B: 1*1000 + 1e-2*1e6 = 11000
        # B outranks A because extreme Pc dominates — correct behavior
        cdms = [
            self._cdm("1", "SAT1", "1", "COSMOS DEB A", "A", 1e-5),
            self._cdm("2", "SAT2", "2", "COSMOS DEB A", "A", 1e-5),
            self._cdm("3", "SAT3", "3", "COSMOS DEB A", "A", 1e-5),
            self._cdm("4", "SAT4", "4", "FENGYUN DEB B", "B", 1e-2),
        ]
        r = compute_rankings(cdms, {})
        assert r["all"][0]["norad_id"] == "B"  # higher threat_score due to extreme Pc
        assert r["all"][1]["norad_id"] == "A"
        assert r["all"][0]["threat_score"] > r["all"][1]["threat_score"]

    def test_band_classification(self):
        cdms = [
            self._cdm("1", "SAT", "10", "COSMOS 1408 DEB LOW", "L", 1e-4),
            self._cdm("2", "SAT", "11", "SL-16 DEB MID", "M", 1e-4),
            self._cdm("3", "SAT", "12", "DELTA DEB HIGH", "H", 1e-4),
        ]
        alts = {"L": 550, "M": 1100, "H": 1500}
        r = compute_rankings(cdms, alts)
        low_ids = [e["norad_id"] for e in r["low"]]
        mid_ids = [e["norad_id"] for e in r["mid"]]
        assert "L" in low_ids and "L" not in mid_ids
        assert "M" in mid_ids and "M" not in low_ids
        assert "H" not in low_ids and "H" not in mid_ids

    def test_dedup_by_cdm_id(self):
        # Same cdm_id twice — must count once
        cdms = [
            self._cdm("DUPE", "SAT", "1", "COSMOS DEB", "D", 1e-4),
            self._cdm("DUPE", "SAT", "1", "COSMOS DEB", "D", 1e-4),
        ]
        r = compute_rankings(cdms, {})
        assert r["all"][0]["cdm_count"] == 1

    def test_active_vs_active_ignored(self):
        # No debris on either side → nothing in output
        cdms = [self._cdm("X", "STARLINK-1", "1", "ONEWEB-1", "2", 1e-4)]
        r = compute_rankings(cdms, {})
        assert r == {"all": [], "low": [], "mid": []}
