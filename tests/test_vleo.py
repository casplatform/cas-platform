#!/usr/bin/env python3
"""
CAS VLEO Module — Unit Tests
pytest tests/test_vleo.py -v
"""
import math
import sys
import os
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from vleo import (
    detect_regime, atmosphere_density, drag_acceleration,
    gravity_acceleration, drag_sigma_inflation, propagate_with_drag,
    estimate_orbital_lifetime, altitude_from_state, orbital_velocity,
    orbital_period, drag_delta_v_per_orbit, vleo_conjunction_assessment,
    EARTH_RADIUS_KM, EARTH_MU_M3S2
)


# ═══════════════════════════════════════════════════
# REGIME DETECTION
# ═══════════════════════════════════════════════════
class TestDetectRegime:
    def test_vleo_200km(self):
        assert detect_regime(200) == "vleo"

    def test_vleo_350km(self):
        assert detect_regime(350) == "vleo"

    def test_vleo_399km(self):
        assert detect_regime(399) == "vleo"

    def test_hybrid_400km(self):
        assert detect_regime(400) == "hybrid"

    def test_hybrid_449km(self):
        assert detect_regime(449) == "hybrid"

    def test_leo_450km(self):
        assert detect_regime(450) == "leo"

    def test_leo_800km(self):
        assert detect_regime(800) == "leo"

    def test_vleo_zero(self):
        assert detect_regime(0) == "vleo"

    def test_negative_altitude(self):
        assert detect_regime(-100) == "vleo"


# ═══════════════════════════════════════════════════
# ATMOSPHERE DENSITY
# ═══════════════════════════════════════════════════
class TestAtmosphereDensity:
    def test_density_decreases_with_altitude(self):
        """Density must monotonically decrease with altitude."""
        prev = atmosphere_density(100)
        for alt in range(150, 1001, 50):
            rho = atmosphere_density(alt)
            assert rho < prev, f"Density increased at {alt}km: {rho} >= {prev}"
            prev = rho

    def test_200km_order_of_magnitude(self):
        """200km density should be ~1e-10 to 1e-9 range."""
        rho = atmosphere_density(200)
        assert 1e-11 < rho < 1e-8

    def test_400km_order_of_magnitude(self):
        """400km density should be ~1e-12 range."""
        rho = atmosphere_density(400)
        assert 1e-13 < rho < 1e-11

    def test_zero_altitude(self):
        rho = atmosphere_density(0)
        assert rho == 0.0

    def test_negative_altitude(self):
        rho = atmosphere_density(-50)
        assert rho == 0.0

    def test_very_high_altitude(self):
        """Above 1000km should still return a positive value."""
        rho = atmosphere_density(1500)
        assert rho > 0
        assert rho < atmosphere_density(1000)

    def test_known_reference_200km(self):
        """USSA76 reference: ~2.79e-10 at 200km."""
        rho = atmosphere_density(200)
        assert abs(rho - 2.789e-10) / 2.789e-10 < 0.01  # within 1%

    def test_known_reference_300km(self):
        """USSA76 reference: ~2.42e-11 at 300km."""
        rho = atmosphere_density(300)
        assert abs(rho - 2.418e-11) / 2.418e-11 < 0.01


# ═══════════════════════════════════════════════════
# DRAG ACCELERATION
# ═══════════════════════════════════════════════════
class TestDragAcceleration:
    def _circular_state(self, alt_km):
        r = (EARTH_RADIUS_KM + alt_km) * 1000.0
        v = orbital_velocity(alt_km)
        return (r, 0.0, 0.0), (0.0, v, 0.0)

    def test_drag_opposes_velocity(self):
        """Drag must decelerate the spacecraft."""
        pos, vel = self._circular_state(300)
        ax, ay, az = drag_acceleration(pos, vel)
        # Velocity is +y, drag should have negative y component
        assert ay < 0

    def test_drag_magnitude_increases_at_lower_altitude(self):
        pos200, vel200 = self._circular_state(200)
        pos400, vel400 = self._circular_state(400)
        a200 = math.sqrt(sum(x**2 for x in drag_acceleration(pos200, vel200)))
        a400 = math.sqrt(sum(x**2 for x in drag_acceleration(pos400, vel400)))
        assert a200 > a400 * 10  # at least 10x stronger at 200km vs 400km

    def test_high_ballistic_coef_reduces_drag(self):
        """Higher B = more massive/compact = less drag."""
        pos, vel = self._circular_state(300)
        a_low_b = math.sqrt(sum(x**2 for x in drag_acceleration(pos, vel, 20)))
        a_high_b = math.sqrt(sum(x**2 for x in drag_acceleration(pos, vel, 200)))
        assert a_low_b > a_high_b * 5

    def test_zero_velocity_negligible_drag(self):
        """Zero inertial velocity: only Earth rotation creates tiny v_rel."""
        pos = ((EARTH_RADIUS_KM + 300) * 1000, 0, 0)
        vel = (0, 0, 0)
        ax, ay, az = drag_acceleration(pos, vel)
        # Should be negligibly small compared to orbital drag
        mag = math.sqrt(ax**2 + ay**2 + az**2)
        assert mag < 1e-6  # effectively zero vs ~1e-5 orbital drag

    def test_above_1000km_no_drag(self):
        pos, vel = self._circular_state(1100)
        ax, ay, az = drag_acceleration(pos, vel)
        assert ax == 0 and ay == 0 and az == 0


# ═══════════════════════════════════════════════════
# GRAVITY ACCELERATION
# ═══════════════════════════════════════════════════
class TestGravityAcceleration:
    def test_surface_gravity(self):
        """Should be ~9.8 m/s² at Earth surface."""
        r = EARTH_RADIUS_KM * 1000.0
        ax, ay, az = gravity_acceleration((r, 0, 0))
        g = abs(ax)
        assert 9.5 < g < 10.0

    def test_points_toward_earth(self):
        r = (EARTH_RADIUS_KM + 300) * 1000.0
        ax, ay, az = gravity_acceleration((r, 0, 0))
        assert ax < 0  # points toward origin

    def test_300km_gravity(self):
        """~8.9 m/s² at 300km altitude."""
        r = (EARTH_RADIUS_KM + 300) * 1000.0
        ax, _, _ = gravity_acceleration((r, 0, 0))
        assert 8.5 < abs(ax) < 9.3


# ═══════════════════════════════════════════════════
# SIGMA INFLATION
# ═══════════════════════════════════════════════════
class TestSigmaInflation:
    def test_leo_no_inflation(self):
        assert drag_sigma_inflation(500) == 1.0

    def test_vleo_inflated(self):
        assert drag_sigma_inflation(300) > 2.0

    def test_lower_altitude_more_inflation(self):
        assert drag_sigma_inflation(200) > drag_sigma_inflation(300)
        assert drag_sigma_inflation(300) > drag_sigma_inflation(400)

    def test_high_solar_activity_increases(self):
        quiet = drag_sigma_inflation(300, f107_flux=70)
        active = drag_sigma_inflation(300, f107_flux=250)
        assert active > quiet

    def test_high_kp_increases(self):
        calm = drag_sigma_inflation(300, kp_index=1)
        storm = drag_sigma_inflation(300, kp_index=7)
        assert storm > calm

    def test_450km_no_inflation(self):
        """450km is LEO threshold — no inflation."""
        assert drag_sigma_inflation(450) == 1.0


# ═══════════════════════════════════════════════════
# ORBITAL MECHANICS HELPERS
# ═══════════════════════════════════════════════════
class TestOrbitalHelpers:
    def test_velocity_iss_altitude(self):
        """ISS at ~400km: ~7.67 km/s."""
        v = orbital_velocity(400)
        assert 7600 < v < 7750

    def test_period_iss_altitude(self):
        """ISS at ~400km: ~92 min = ~5520 seconds."""
        T = orbital_period(400)
        assert 5400 < T < 5700

    def test_altitude_from_state(self):
        r = (EARTH_RADIUS_KM + 350) * 1000.0
        alt = altitude_from_state((r, 0, 0))
        assert abs(alt - 350) < 0.01

    def test_dv_per_orbit_200km(self):
        """At 200km with B=50, should be ~0.5-1.5 m/s/orbit."""
        dv = drag_delta_v_per_orbit(200, 50)
        assert 0.3 < dv < 2.0

    def test_dv_per_orbit_decreases_with_altitude(self):
        dv200 = drag_delta_v_per_orbit(200)
        dv400 = drag_delta_v_per_orbit(400)
        assert dv200 > dv400 * 20


# ═══════════════════════════════════════════════════
# PROPAGATION
# ═══════════════════════════════════════════════════
class TestPropagation:
    def _circular_state(self, alt_km):
        r = (EARTH_RADIUS_KM + alt_km) * 1000.0
        v = orbital_velocity(alt_km)
        return (r, 0.0, 0.0), (0.0, v, 0.0)

    def test_no_drag_conserves_altitude(self):
        """With negligible drag, altitude should be preserved."""
        pos, vel = self._circular_state(300)
        T = orbital_period(300)
        pos1, vel1 = propagate_with_drag(pos, vel, T, ballistic_coef=1e12)
        alt1 = altitude_from_state(pos1)
        assert abs(alt1 - 300) < 0.5  # within 500m

    def test_drag_decreases_altitude(self):
        """With drag, altitude should decrease after one orbit."""
        pos, vel = self._circular_state(300)
        T = orbital_period(300)
        pos1, vel1 = propagate_with_drag(pos, vel, T, ballistic_coef=50)
        alt1 = altitude_from_state(pos1)
        assert alt1 < 300.0

    def test_lower_bc_faster_decay(self):
        """Lower ballistic coefficient = faster altitude decay."""
        pos, vel = self._circular_state(300)
        T = orbital_period(300)
        _, _ = propagate_with_drag(pos, vel, T, ballistic_coef=20)
        pos_high, _ = propagate_with_drag(pos, vel, T, ballistic_coef=20)
        pos_low, _ = propagate_with_drag(pos, vel, T, ballistic_coef=200)
        alt_high = altitude_from_state(pos_high)
        alt_low = altitude_from_state(pos_low)
        assert alt_high < alt_low  # B=20 decays faster

    def test_zero_dt_returns_same(self):
        pos, vel = self._circular_state(400)
        pos1, vel1 = propagate_with_drag(pos, vel, 0)
        assert pos1 == pos
        assert vel1 == vel


# ═══════════════════════════════════════════════════
# ORBITAL LIFETIME
# ═══════════════════════════════════════════════════
class TestOrbitalLifetime:
    def test_200km_very_short(self):
        """200km should decay in days, not months."""
        days = estimate_orbital_lifetime(200, 50)
        assert 0.1 < days < 10

    def test_500km_years(self):
        """500km should last years."""
        days = estimate_orbital_lifetime(500, 50)
        assert days > 365

    def test_below_threshold_zero(self):
        assert estimate_orbital_lifetime(100, 50) == 0.0

    def test_higher_altitude_longer(self):
        d300 = estimate_orbital_lifetime(300, 50)
        d400 = estimate_orbital_lifetime(400, 50)
        assert d400 > d300 * 3

    def test_higher_bc_longer(self):
        """More massive spacecraft lasts longer."""
        d_light = estimate_orbital_lifetime(300, 20)
        d_heavy = estimate_orbital_lifetime(300, 200)
        assert d_heavy > d_light * 5


# ═══════════════════════════════════════════════════
# CONJUNCTION ASSESSMENT
# ═══════════════════════════════════════════════════
class TestVLEOAssessment:
    def test_leo_standard_analysis(self):
        result = vleo_conjunction_assessment(500, 200, 1e-4)
        assert result["regime"] == "leo"
        assert result["sigma_inflation"] == 1.0
        # Label carries a dynamic urgency score; match the stable prefix only.
        assert result["decision_label"].startswith("Standard LEO analysis")
        assert result["recommendation"] is None

    def test_vleo_monitor_only(self):
        result = vleo_conjunction_assessment(300, 200, 1e-3)
        assert result["regime"] == "vleo"
        assert result["sigma_inflation"] > 2.0
        assert result["decision_label"].startswith("VLEO drag-aware assessment")
        assert result["recommendation"] is not None

    def test_hybrid_regime(self):
        result = vleo_conjunction_assessment(420, 200, 1e-4)
        assert result["regime"] == "hybrid"
        assert result["sigma_inflation"] > 1.0

    def test_contains_all_fields(self):
        result = vleo_conjunction_assessment(300, 500, 1e-3)
        required_fields = [
            "regime", "altitude_km", "sigma_inflation",
            "atmosphere_density_kg_m3", "pc_standard",
            "pc_confidence_note", "estimated_lifetime_days",
            "drag_dv_per_orbit_ms", "decision_label", "recommendation"
        ]
        for field in required_fields:
            assert field in result, f"Missing field: {field}"

    def test_solar_storm_increases_inflation(self):
        calm = vleo_conjunction_assessment(300, 200, 1e-3, kp_index=1, f107_flux=70)
        storm = vleo_conjunction_assessment(300, 200, 1e-3, kp_index=7, f107_flux=250)
        assert storm["sigma_inflation"] > calm["sigma_inflation"]
