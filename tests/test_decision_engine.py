"""
Unit tests for DecisionEngine._compute_priority / _recommendation / _confidence.

DecisionEngine requires a db_url in __init__ but the tested methods don't
touch the DB — we pass a dummy URL.
"""
import pytest
from cas_engine import DecisionEngine

H = 3600  # seconds


@pytest.fixture
def engine():
    return DecisionEngine(db_url="postgresql://dummy:dummy@127.0.0.1:1/nodb")


# ── Priority ─────────────────────────────────────────
class TestComputePriority:
    def test_high_all_factors_max(self, engine):
        # Pc ≥ RED (40) + miss < CRITICAL (30) + urgent (30) = 100 → HIGH
        assert engine._compute_priority(1e-3, 100, 6 * H) == "HIGH"

    def test_high_red_pc_plus_urgent(self, engine):
        # 40 + 0 + 30 = 70 → HIGH
        assert engine._compute_priority(1e-3, 5000, 6 * H) == "HIGH"

    def test_medium_yellow_and_warning_miss(self, engine):
        # Pc YELLOW (25) + miss < WARNING (15) + no timing (0) = 40 → MEDIUM
        assert engine._compute_priority(5e-5, 500, None) == "MEDIUM"

    def test_low_green_no_timing(self, engine):
        # Pc < GREEN_THRESHOLD (0) + miss > 5000 (0) = 0 → LOW
        assert engine._compute_priority(1e-9, 10000, None) == "LOW"

    def test_none_time_remaining_safe(self, engine):
        # Should not crash; just skips the timing contribution
        result = engine._compute_priority(1e-3, 100, None)
        assert result in ("HIGH", "MEDIUM", "LOW")

    def test_priority_monotonic_in_pc(self, engine):
        # Holding miss + time fixed, higher Pc never yields lower priority
        miss, t = 5000, None
        order = {"LOW": 0, "MEDIUM": 1, "HIGH": 2}
        p_low = order[engine._compute_priority(1e-9, miss, t)]
        p_yel = order[engine._compute_priority(5e-5, miss, t)]
        p_red = order[engine._compute_priority(1e-3, miss, t)]
        assert p_low <= p_yel <= p_red


# ── Recommendation ───────────────────────────────────
class TestComputeRecommendation:
    def test_red_risk_gives_maneuver(self, engine):
        assert engine._compute_recommendation(1e-3, 500, 12 * H, "RED") \
            == "Maneuver advised"

    def test_high_pc_gives_maneuver_even_with_green_label(self, engine):
        # Pc ≥ PC_RED_THRESHOLD forces Maneuver regardless of risk label
        assert engine._compute_recommendation(1e-4, 5000, 12 * H, "GREEN") \
            == "Maneuver advised"

    def test_critical_miss_gives_maneuver(self, engine):
        assert engine._compute_recommendation(0, 100, 12 * H, "GREEN") \
            == "Maneuver advised"

    def test_tca_passed_downgrades_to_monitor(self, engine):
        # time_remaining_s <= 0 → Monitor even if RED
        assert engine._compute_recommendation(1e-3, 100, -600, "RED") \
            == "Monitor"

    def test_yellow_gives_monitor(self, engine):
        assert engine._compute_recommendation(5e-5, 5000, 24 * H, "YELLOW") \
            == "Monitor"

    def test_low_risk_gives_no_action(self, engine):
        assert engine._compute_recommendation(1e-9, 10000, 48 * H, "GREEN") \
            == "No action"


# ── Confidence ───────────────────────────────────────
class TestComputeConfidence:
    def test_close_to_tca_extreme_pc_high(self, engine):
        # <12h + very high Pc → high
        assert engine._compute_confidence(1e-3, 500, 6 * H) == "high"

    def test_close_to_tca_extreme_low_pc_high(self, engine):
        # <12h + very low Pc (< 1e-8) → high
        assert engine._compute_confidence(1e-9, 5000, 6 * H) == "high"

    def test_close_to_tca_middling_pc_medium(self, engine):
        # <12h + middling Pc → medium
        assert engine._compute_confidence(1e-5, 500, 6 * H) == "medium"

    def test_far_from_tca_low_pc_low(self, engine):
        # >48h + low Pc → low
        assert engine._compute_confidence(1e-6, 5000, 96 * H) == "low"

    def test_far_from_tca_very_high_pc_medium(self, engine):
        # >48h + Pc ≥ 1e-2 → medium
        assert engine._compute_confidence(5e-2, 500, 96 * H) == "medium"

    def test_no_timing_red_pc_medium(self, engine):
        # time_remaining_s None + Pc ≥ RED threshold → medium
        assert engine._compute_confidence(1e-3, 500, None) == "medium"

    def test_no_timing_low_pc_low(self, engine):
        assert engine._compute_confidence(1e-9, 5000, None) == "low"


# ── Integration sanity ───────────────────────────────
class TestDecisionEngineIntegration:
    def test_engine_instantiates_without_db(self, engine):
        # We never call evaluate_conjunction() (which would connect);
        # just confirm the object exists and exposes the thresholds.
        assert engine.PC_RED_THRESHOLD == 1e-4
        assert engine.MISS_CRITICAL_M == 200
        assert engine.URGENT_WINDOW_S == 24 * 3600

    def test_thresholds_are_ordered(self, engine):
        assert engine.PC_RED_THRESHOLD > engine.PC_YELLOW_THRESHOLD \
            > engine.PC_GREEN_THRESHOLD
        assert engine.MISS_CRITICAL_M < engine.MISS_WARNING_M
        assert engine.URGENT_WINDOW_S < engine.WARNING_WINDOW_S \
            < engine.PLANNING_WINDOW_S
