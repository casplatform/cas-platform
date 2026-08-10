"""
Unit tests for collision_probability() — Foster's method (1992) single-variable
reduction of the 2D Gaussian integral using modified Bessel function I0.

Reference: Foster, J.L. & Estes, H.S. (1992). "A parametric analysis of orbital
debris collision probability and maneuver rate for space vehicles."
NASA/JSC-25898. Also cross-referenced with Chan, F.K. (2008) "Spacecraft
Collision Probability", AIAA.

These tests establish defensible baseline behavior for TRL 5 Validation Report.
"""
import math
import pytest
from cas_engine import collision_probability, _bessel_i0


class TestBesselI0:
    """Sanity checks for the modified Bessel function I0 helper."""

    def test_i0_at_zero_is_one(self):
        # I0(0) = 1 by definition
        assert _bessel_i0(0) == 1.0

    def test_i0_small_arg(self):
        # I0(1) ≈ 1.2660658732...
        assert abs(_bessel_i0(1.0) - 1.2660658732) < 1e-6

    def test_i0_large_arg(self):
        # I0(5) ≈ 27.2398718
        assert abs(_bessel_i0(5.0) - 27.2398718) < 1e-3

    def test_i0_symmetric(self):
        # I0 is an even function: I0(-x) == I0(x)
        assert abs(_bessel_i0(-2.5) - _bessel_i0(2.5)) < 1e-9


class TestCollisionProbability:
    """Unit tests for Foster-integral Pc implementation."""

    def test_sigma_below_threshold_returns_zero(self):
        # Guard clause: sigma < 1e-3 → 0.0 (prevents division by zero)
        assert collision_probability(miss_m=100, sigma=0) == 0.0
        assert collision_probability(miss_m=100, sigma=1e-6) == 0.0

    def test_zero_miss_high_pc(self):
        # Head-on (miss=0) with small sigma and HBR=10 should give large Pc.
        # Analytical for miss=0: Pc = 1 - exp(-s²/2) where s = HBR/sigma.
        # sigma=30, HBR=10: s=1/3, Pc_expected ≈ 1-exp(-0.0556) ≈ 0.05406
        pc = collision_probability(miss_m=0, sigma=30, hbr=10)
        expected = 1 - math.exp(-0.5 * (10/30)**2)
        assert abs(pc - expected) < 1e-3, f"pc={pc}, expected≈{expected}"

    def test_far_miss_near_zero_pc(self):
        # miss_m >> sigma → Pc should be numerically negligible (<1e-15).
        pc = collision_probability(miss_m=10000, sigma=50, hbr=10)
        assert pc < 1e-15, f"expected ~0, got {pc}"

    def test_monotonic_decrease_with_miss(self):
        # Holding sigma and HBR fixed, Pc must decrease as miss distance grows.
        pcs = [collision_probability(miss_m=m, sigma=50, hbr=10)
               for m in (10, 50, 100, 200, 500)]
        for i in range(len(pcs) - 1):
            assert pcs[i] > pcs[i+1], f"non-monotonic at index {i}: {pcs}"

    def test_hbr_area_scaling(self):
        # For small HBR/sigma ratios, Pc scales approximately with HBR²
        # (probability density × area). Doubling HBR should ≈ 4× Pc.
        pc1 = collision_probability(miss_m=100, sigma=50, hbr=5)
        pc2 = collision_probability(miss_m=100, sigma=50, hbr=10)
        ratio = pc2 / pc1
        assert 3.5 < ratio < 4.5, f"HBR²-scaling broken: ratio={ratio:.3f}"

    def test_bounded_range(self):
        # Pc must always be in [0, 1] regardless of input combinations.
        test_inputs = [
            (0, 1, 10),     # extreme: zero miss, tiny sigma
            (50000, 1000, 10),  # large miss
            (100, 50, 100),  # very large HBR
        ]
        for miss, sigma, hbr in test_inputs:
            pc = collision_probability(miss, sigma, hbr)
            assert 0.0 <= pc <= 1.0, f"Pc={pc} out of [0,1] for {miss,sigma,hbr}"

    def test_cara_reference_point(self):
        # Reference: typical LEO high-risk screening value.
        # miss=100m, sigma=50m, HBR=10m → Pc in the low 1e-3 range,
        # consistent with published Foster-integral evaluations.
        pc = collision_probability(miss_m=100, sigma=50, hbr=10)
        assert 1e-4 < pc < 1e-2, f"reference point out of expected band: {pc}"
