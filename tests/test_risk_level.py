"""
Unit tests for risk_level(Pc, miss_m) classifier.

Thresholds (from cas_engine.py):
  RED    : Pc > 1e-4  OR  miss_m < 200
  YELLOW : Pc > 1e-5  OR  miss_m < 1000
  GREEN  : otherwise
"""
import pytest
from cas_engine import risk_level


class TestRiskLevelRed:
    def test_high_pc_red(self):
        assert risk_level(1.5e-4, 5000) == "RED"

    def test_close_miss_red(self):
        assert risk_level(0, 150) == "RED"

    def test_red_dominates_when_both(self):
        assert risk_level(1e-3, 50) == "RED"


class TestRiskLevelYellow:
    def test_moderate_pc_yellow(self):
        assert risk_level(5e-5, 5000) == "YELLOW"

    def test_moderate_miss_yellow(self):
        assert risk_level(0, 500) == "YELLOW"


class TestRiskLevelGreen:
    def test_low_pc_far_miss_green(self):
        assert risk_level(1e-9, 10000) == "GREEN"

    def test_zero_pc_far_miss_green(self):
        assert risk_level(0, 50000) == "GREEN"


class TestRiskLevelBoundaries:
    def test_pc_just_above_red_threshold(self):
        # 1e-4 is NOT > 1e-4, so exactly-at-threshold falls through to YELLOW
        assert risk_level(1e-4, 5000) == "YELLOW"
        # A hair above → RED
        assert risk_level(1.0001e-4, 5000) == "RED"

    def test_miss_just_at_red_boundary(self):
        # miss_m < 200 is RED; exactly 200 is NOT (falls through)
        assert risk_level(0, 199) == "RED"
        assert risk_level(0, 200) == "YELLOW"  # 200 < 1000 → YELLOW
