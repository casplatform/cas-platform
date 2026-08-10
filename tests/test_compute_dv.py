"""
Unit tests for compute_dv() — binary search delta-V computation.
Tests for TrendAnalyzer._simple_forecast and _empty_forecast.
"""
import pytest
from cas_engine import compute_dv, collision_probability, TrendAnalyzer


class TestComputeDv:
    """Tests for the ΔV binary search solver."""

    def test_returns_positive_float(self):
        dv = compute_dv(miss_m=100, sigma=50, lead_s=3600)
        assert isinstance(dv, float)
        assert dv > 0

    def test_higher_miss_needs_less_dv(self):
        # Object already far → needs less ΔV to reach target Pc
        dv_close = compute_dv(miss_m=50, sigma=50, lead_s=3600)
        dv_far = compute_dv(miss_m=500, sigma=50, lead_s=3600)
        assert dv_close > dv_far

    def test_shorter_lead_needs_more_dv(self):
        # Less time to act → needs more ΔV for same displacement
        dv_long = compute_dv(miss_m=100, sigma=50, lead_s=7200)
        dv_short = compute_dv(miss_m=100, sigma=50, lead_s=1800)
        assert dv_short > dv_long

    def test_result_achieves_target_pc(self):
        # Verify the returned ΔV actually reduces Pc below target
        miss_m, sigma, lead_s, target = 100, 50, 3600, 1e-6
        dv = compute_dv(miss_m, sigma, lead_s, target)
        new_miss = miss_m + dv * lead_s * 0.5
        actual_pc = collision_probability(new_miss, sigma)
        assert actual_pc <= target, f"Pc={actual_pc} still above target={target}"

    def test_stricter_target_needs_more_dv(self):
        # Lower target Pc → more ΔV needed
        dv_loose = compute_dv(miss_m=100, sigma=50, lead_s=3600, target_Pc=1e-4)
        dv_strict = compute_dv(miss_m=100, sigma=50, lead_s=3600, target_Pc=1e-8)
        assert dv_strict > dv_loose

    def test_precision_four_decimals(self):
        dv = compute_dv(miss_m=200, sigma=100, lead_s=3600)
        # round(x, 4) means at most 4 decimal places
        assert dv == round(dv, 4)


class TestSimpleForecast:
    """Tests for TrendAnalyzer._simple_forecast — linear Pc projection."""

    @pytest.fixture
    def analyzer(self):
        return TrendAnalyzer.__new__(TrendAnalyzer)

    def test_stable_trend(self, analyzer):
        f = analyzer._simple_forecast(current_pc=1e-5, slope_per_hour=0)
        assert f["risk_direction"] == "stable"
        assert f["forecasts"]["72h"]["projected_pc"] == pytest.approx(1e-5)

    def test_escalating_trend(self, analyzer):
        # Slope positive and large enough to double Pc in 72h
        f = analyzer._simple_forecast(current_pc=1e-5, slope_per_hour=1e-5)
        assert f["risk_direction"] == "escalating"
        assert f["forecasts"]["72h"]["projected_pc"] > 1e-5

    def test_deescalating_trend(self, analyzer):
        f = analyzer._simple_forecast(current_pc=1e-3, slope_per_hour=-1e-4)
        assert f["risk_direction"] == "de-escalating"

    def test_forecast_risk_levels(self, analyzer):
        # High projected Pc → RED
        f = analyzer._simple_forecast(current_pc=1e-4, slope_per_hour=1e-5)
        assert f["forecasts"]["72h"]["projected_risk"] == "RED"
        # Low projected Pc → GREEN
        f2 = analyzer._simple_forecast(current_pc=1e-9, slope_per_hour=0)
        assert f2["forecasts"]["12h"]["projected_risk"] == "GREEN"

    def test_pc_clamped_to_bounds(self, analyzer):
        # Negative slope shouldn't produce negative Pc
        f = analyzer._simple_forecast(current_pc=1e-8, slope_per_hour=-1e-3)
        for key in ("12h", "24h", "48h", "72h"):
            assert f["forecasts"][key]["projected_pc"] >= 0
        # Extreme positive slope capped at 1.0
        f2 = analyzer._simple_forecast(current_pc=0.5, slope_per_hour=0.1)
        for key in ("12h", "24h", "48h", "72h"):
            assert f2["forecasts"][key]["projected_pc"] <= 1.0

    def test_confidence_decreases_with_horizon(self, analyzer):
        f = analyzer._simple_forecast(current_pc=1e-4, slope_per_hour=1e-6)
        assert f["forecasts"]["12h"]["confidence"] == "high"
        assert f["forecasts"]["24h"]["confidence"] == "high"
        assert f["forecasts"]["48h"]["confidence"] == "medium"
        assert f["forecasts"]["72h"]["confidence"] == "low"

    def test_four_forecast_horizons(self, analyzer):
        f = analyzer._simple_forecast(current_pc=1e-5, slope_per_hour=0)
        assert set(f["forecasts"].keys()) == {"12h", "24h", "48h", "72h"}


class TestEmptyForecast:
    """Tests for TrendAnalyzer._empty_forecast — default/fallback."""

    @pytest.fixture
    def analyzer(self):
        return TrendAnalyzer.__new__(TrendAnalyzer)

    def test_returns_unknown_direction(self, analyzer):
        f = analyzer._empty_forecast()
        assert f["risk_direction"] == "unknown"

    def test_all_green(self, analyzer):
        f = analyzer._empty_forecast()
        for key in ("12h", "24h", "48h", "72h"):
            assert f["forecasts"][key]["projected_risk"] == "GREEN"
            assert f["forecasts"][key]["projected_pc"] == 0

    def test_four_horizons(self, analyzer):
        f = analyzer._empty_forecast()
        assert set(f["forecasts"].keys()) == {"12h", "24h", "48h", "72h"}
