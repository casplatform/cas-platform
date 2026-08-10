"""
Decision logic tests: decision_scanner saf fonksiyonlari.

classify_risk(pc) ve evaluate_conjunctions(conjunctions, ...) DB gerektirmez,
saf fonksiyonlardir. Bu testler CAS'in DETERMINISTIK karar mantiginin
golden reference'idir - ML Layer 1 (false positive reduction) gelmeden once
mevcut davranisi kilitler.

Pc esikleri:
  RED:    pc >= 1e-4
  YELLOW: pc >= 1e-5
  GREEN:  pc <  1e-5

Karar matrisi (evaluate_conjunctions):
  2+ RED veya max_pc >= 1e-2  -> CRITICAL, "Maneuver advised"
  1 RED  veya max_pc >= 1e-3  -> HIGH,     "Maneuver advised"
  2+ YEL veya max_pc >= 1e-4  -> MEDIUM,   "Monitor"
  else                        -> LOW,      "No action"
"""
import pytest
import sys
sys.path.insert(0, "/opt/cas")
from decision_scanner import classify_risk, evaluate_conjunctions


class TestClassifyRisk:
    def test_red_threshold(self):
        assert classify_risk(1e-4) == "RED"
        assert classify_risk(1e-3) == "RED"
        assert classify_risk(0.5) == "RED"

    def test_yellow_threshold(self):
        assert classify_risk(1e-5) == "YELLOW"
        assert classify_risk(5e-5) == "YELLOW"
        assert classify_risk(9.9e-5) == "YELLOW"

    def test_green_threshold(self):
        assert classify_risk(1e-6) == "GREEN"
        assert classify_risk(0) == "GREEN"
        assert classify_risk(9.9e-6) == "GREEN"

    def test_boundary_exact(self):
        # Tam esik degerleri - >= oldugu icin
        assert classify_risk(1e-4) == "RED"      # tam 1e-4 -> RED
        assert classify_risk(1e-5) == "YELLOW"   # tam 1e-5 -> YELLOW
        assert classify_risk(9.999e-5) == "YELLOW"  # 1e-4'un hemen altinda


class TestEvaluateConjunctions:
    def test_empty_conjunctions(self):
        result = evaluate_conjunctions([], "TESTSAT", "12345")
        assert result["recommendation"] == "no_action"
        assert result["priority"] == "LOW"
        assert result["total_conjunctions"] == 0
        assert result["max_pc"] == 0

    def test_two_red_critical(self):
        conj = [
            {"risk": "RED", "pc": 5e-4, "miss_distance_m": 200},
            {"risk": "RED", "pc": 3e-4, "miss_distance_m": 300},
        ]
        result = evaluate_conjunctions(conj, "SAT", "1")
        assert result["priority"] == "CRITICAL"
        assert result["recommendation"] == "Maneuver advised"
        assert result["red_count"] == 2

    def test_high_pc_critical(self):
        """max_pc >= 1e-2 tek basina CRITICAL."""
        conj = [{"risk": "RED", "pc": 2e-2, "miss_distance_m": 100}]
        result = evaluate_conjunctions(conj, "SAT", "1")
        assert result["priority"] == "CRITICAL"

    def test_one_red_high(self):
        conj = [{"risk": "RED", "pc": 5e-4, "miss_distance_m": 500}]
        result = evaluate_conjunctions(conj, "SAT", "1")
        assert result["priority"] == "HIGH"
        assert result["recommendation"] == "Maneuver advised"

    def test_two_yellow_medium(self):
        conj = [
            {"risk": "YELLOW", "pc": 5e-5, "miss_distance_m": 800},
            {"risk": "YELLOW", "pc": 3e-5, "miss_distance_m": 900},
        ]
        result = evaluate_conjunctions(conj, "SAT", "1")
        assert result["priority"] == "MEDIUM"
        assert result["recommendation"] == "Monitor"

    def test_single_green_low(self):
        conj = [{"risk": "GREEN", "pc": 1e-7, "miss_distance_m": 5000}]
        result = evaluate_conjunctions(conj, "SAT", "1")
        assert result["priority"] == "LOW"
        assert result["recommendation"] == "No action"

    def test_counts_accurate(self):
        conj = [
            {"risk": "RED", "pc": 5e-4, "miss_distance_m": 200},
            {"risk": "YELLOW", "pc": 5e-5, "miss_distance_m": 800},
            {"risk": "GREEN", "pc": 1e-7, "miss_distance_m": 5000},
        ]
        result = evaluate_conjunctions(conj, "SAT", "1")
        assert result["red_count"] == 1
        assert result["yellow_count"] == 1
        assert result["green_count"] == 1
        assert result["total_conjunctions"] == 3
        assert result["alert_review"] == 2  # red + yellow
        assert result["alert_critical"] == 1  # red

    def test_maneuver_summary_close_miss(self):
        """Maneuver advised + min_miss < 1000 -> maneuver_summary dolu."""
        conj = [{"risk": "RED", "pc": 5e-4, "miss_distance_m": 300}]
        result = evaluate_conjunctions(conj, "SAT", "1")
        assert result["maneuver_summary"] is not None
        assert result["delta_v_ms"] is not None
        assert result["maneuver_direction"] == "prograde"

    def test_no_maneuver_summary_far_miss(self):
        """Maneuver advised ama min_miss >= 1000 -> maneuver_summary None."""
        conj = [{"risk": "RED", "pc": 5e-4, "miss_distance_m": 2000}]
        result = evaluate_conjunctions(conj, "SAT", "1")
        assert result["maneuver_summary"] is None

    def test_max_pc_extracted(self):
        conj = [
            {"risk": "YELLOW", "pc": 3e-5, "miss_distance_m": 800},
            {"risk": "RED", "pc": 5e-4, "miss_distance_m": 200},
        ]
        result = evaluate_conjunctions(conj, "SAT", "1")
        assert result["max_pc"] == 5e-4
