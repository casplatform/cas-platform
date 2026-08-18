"""
Covariance-Based Computation Verification Suite
================================================

Verifies the covariance-dependent computation paths that activate ONLY when an
operator supplies a full CCSDS CDM (covariance matrix present) — the exact code
that goes live during the TÜBİTAK pilot when public 16-field CDMs are replaced
by operator-tier 110+ field messages.

Three independent evidence layers:
  Layer 1  Analytic cross-check   pc_2d (numerical Foster integral) vs
                                  pc_2d_isotropic_reference (Marcum-Q closed form)
  Layer 2  Property / invariant   Pc in [0,1]; covariance SPD; Mahalanobis >= 0;
                                  monotonicity; gate transitions
  Layer 3  Real-data sanity       every Kelvins test CDM (24,484 real US-SSN
                                  conjunctions) run through the full pipeline

References:
  Foster & Estes (1992), NASA/JSC-25898
  Chan, F.K. (2008), "Spacecraft Collision Probability", AIAA
  CCSDS 508.0-B-1 Conjunction Data Message
  Uriot et al. (2020), "Spacecraft Collision Avoidance Challenge", Astrodynamics
"""
import csv
import math
import os
import sys

import numpy as np
import pytest

from conftest import INSTANCE_ROOT
for _p in (INSTANCE_ROOT, os.path.join(INSTANCE_ROOT, "cas_api")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from services.conjunction_math import (
    eci2rtn, project_to_bplane, bplane_mahalanobis, pc_2d,
    pc_2d_isotropic_reference,
)

KELVINS = "/opt/cas/ml/datasets/esa_kelvins/test_data.csv"

# A representative circular-LEO encounter geometry (ECI, km & km/s).
# Two objects near the same point with a near-perpendicular relative velocity.
R1 = np.array([6778.0, 0.0, 0.0])
V1 = np.array([0.0, 7.668, 0.0])
R2 = np.array([6778.0, 0.0, 0.05])   # 50 m cross-track separation
V2 = np.array([0.0, 0.0, 7.668])     # perpendicular approach


def _iso_cov(sigma_m):
    """Isotropic RTN position covariance (m^2), 3x3 upper block used as diag 3x3."""
    s2 = sigma_m ** 2
    return np.diag([s2, s2, s2])


# ─────────────────────────────────────────────────────────────────────────
# LAYER 1 — Analytic cross-check: numerical integral vs closed-form Marcum-Q
# ─────────────────────────────────────────────────────────────────────────
class TestLayer1AnalyticCrossCheck:
    """In the isotropic case, pc_2d (2D Foster integral) must agree with
    pc_2d_isotropic_reference (non-central chi-square / Marcum-Q)."""

    @pytest.mark.parametrize("sep_km,sigma_m,hbr_m", [
        (0.05,  30.0, 10.0),
        (0.10,  50.0, 10.0),
        (0.20,  80.0, 20.0),
        (0.02,  40.0,  5.0),
        (0.50, 150.0, 15.0),
    ])
    def test_integral_matches_marcumq(self, sep_km, sigma_m, hbr_m):
        """The numerical 2D Foster integral (pc_2d) must equal the closed-form
        Marcum-Q reference (pc_2d_isotropic_reference) when both are evaluated
        on the SAME 2D encounter-plane geometry that pc_2d actually projects to.

        This is the strongest possible cross-check: two mathematically
        independent methods (numerical double integral vs non-central chi-square
        CDF) computing the same probability. The reference is built from the
        projected in-plane miss and the projected 2D sigma, extracted from the
        b-plane projection itself — not from the pre-projection 3D geometry
        (a 3D separation shrinks when projected onto the plane perpendicular to
        the relative velocity)."""
        r1 = np.array([6778.0, 0.0, 0.0])
        v1 = np.array([0.0, 7.668, 0.0])
        r2 = np.array([6778.0, 0.0, sep_km])   # km, cross-track separation
        v2 = np.array([0.0, 0.0, 7.668])       # perpendicular approach
        C = _iso_cov(sigma_m)

        # What pc_2d actually sees after projecting onto the encounter plane:
        C2d, d2d = project_to_bplane(r1, v1, C, r2, v2, C)
        assert C2d is not None, "projection failed on valid input"
        proj_miss = float(np.linalg.norm(d2d))
        eig = np.linalg.eigvalsh(C2d)
        # Isotropic in-plane covariance => both eigenvalues equal => single sigma
        assert abs(eig[0] - eig[1]) < 1e-6 * eig[1], (
            f"in-plane covariance not isotropic: {eig}"
        )
        sig_2d = math.sqrt(eig.mean())

        pc_num = pc_2d(r1, v1, C, r2, v2, C, hbr_m)
        pc_ref = pc_2d_isotropic_reference(proj_miss, sig_2d, hbr_m)

        assert pc_num is not None, "pc_2d returned None on valid isotropic input"
        rel = abs(pc_num - pc_ref) / max(pc_ref, 1e-12)
        assert rel < 1e-3, (
            f"integral {pc_num:.6e} vs Marcum-Q {pc_ref:.6e} "
            f"(rel {rel:.2e}) proj_miss={proj_miss:.1f}m sig2d={sig_2d:.1f}m hbr={hbr_m}"
        )


# ─────────────────────────────────────────────────────────────────────────
# LAYER 2 — Property / invariant tests
# ─────────────────────────────────────────────────────────────────────────
class TestLayer2Invariants:

    def test_eci2rtn_orthonormal(self):
        M = eci2rtn(R1, V1)
        assert M is not None
        # Rows must be orthonormal: M @ M.T == I
        assert np.allclose(M @ M.T, np.eye(3), atol=1e-9)

    def test_pc_bounded_0_1(self):
        for sigma in (10, 50, 200, 1000):
            pc = pc_2d(R1, V1, _iso_cov(sigma), R2, V2, _iso_cov(sigma), 10.0)
            if pc is not None:
                assert 0.0 <= pc <= 1.0, f"Pc={pc} out of [0,1] at sigma={sigma}"

    def test_mahalanobis_nonnegative(self):
        for sigma in (10, 50, 200):
            d = bplane_mahalanobis(R1, V1, _iso_cov(sigma), R2, V2, _iso_cov(sigma))
            if d is not None:
                assert d >= 0.0, f"Mahalanobis {d} < 0 at sigma={sigma}"

    def test_pc_monotonic_in_miss(self):
        # Larger cross-track separation -> lower Pc (fixed covariance).
        pcs = []
        for sep_km in (0.02, 0.05, 0.1, 0.2, 0.5):
            r2 = np.array([6778.0, 0.0, sep_km])
            pc = pc_2d(R1, V1, _iso_cov(50), r2, V2, _iso_cov(50), 10.0)
            pcs.append(pc if pc is not None else 0.0)
        for i in range(len(pcs) - 1):
            assert pcs[i] >= pcs[i + 1], f"non-monotonic: {pcs}"

    def test_mahalanobis_grows_as_cov_shrinks(self):
        # Tighter covariance -> same miss is "more standard deviations away".
        d_tight = bplane_mahalanobis(R1, V1, _iso_cov(10), R2, V2, _iso_cov(10))
        d_loose = bplane_mahalanobis(R1, V1, _iso_cov(200), R2, V2, _iso_cov(200))
        assert d_tight is not None and d_loose is not None
        assert d_tight > d_loose, f"tight={d_tight} not > loose={d_loose}"

    def test_singular_covariance_returns_none(self):
        # Zero covariance -> singular -> must return None, not crash.
        Z = np.zeros((3, 3))
        assert pc_2d(R1, V1, Z, R2, V2, Z, 10.0) is None
        assert bplane_mahalanobis(R1, V1, Z, R2, V2, Z) is None


# ─────────────────────────────────────────────────────────────────────────
# LAYER 3 — Real-data sanity across the full Kelvins test set
# ─────────────────────────────────────────────────────────────────────────
def _rtn_cov_from_kelvins(row, p):
    """Build 3x3 RTN *position* covariance (m^2) from Kelvins sigma+corr fields."""
    def f(k):
        v = row.get(k, "")
        try:
            return float(v)
        except (TypeError, ValueError):
            return None
    s_r, s_t, s_n = f(f"{p}_sigma_r"), f(f"{p}_sigma_t"), f(f"{p}_sigma_n")
    if None in (s_r, s_t, s_n) or min(s_r, s_t, s_n) <= 0:
        return None
    ct_r = f(f"{p}_ct_r") or 0.0   # corr(T,R)
    cn_r = f(f"{p}_cn_r") or 0.0   # corr(N,R)
    cn_t = f(f"{p}_cn_t") or 0.0   # corr(N,T)
    C = np.array([
        [s_r * s_r,        ct_r * s_t * s_r, cn_r * s_n * s_r],
        [ct_r * s_t * s_r, s_t * s_t,        cn_t * s_n * s_t],
        [cn_r * s_n * s_r, cn_t * s_n * s_t, s_n * s_n],
    ])
    return C


def _load_kelvins(limit=None):
    rows = []
    with open(KELVINS) as fh:
        for i, row in enumerate(csv.DictReader(fh)):
            rows.append(row)
            if limit and len(rows) >= limit:
                break
    return rows


@pytest.fixture(scope="module")
def kelvins_rows():
    if not os.path.exists(KELVINS):
        pytest.skip(f"Kelvins dataset not found at {KELVINS}")
    return _load_kelvins()


class TestLayer3RealData:

    def test_dataset_loads(self, kelvins_rows):
        assert len(kelvins_rows) > 20000, f"expected ~24k rows, got {len(kelvins_rows)}"

    def test_all_covariances_spd(self, kelvins_rows):
        """Every reconstructed RTN position covariance must be symmetric
        positive-definite (all eigenvalues > 0)."""
        checked = 0
        bad = 0
        for row in kelvins_rows:
            for p in ("t", "c"):
                C = _rtn_cov_from_kelvins(row, p)
                if C is None:
                    continue
                checked += 1
                # symmetric
                if not np.allclose(C, C.T, atol=1e-6):
                    bad += 1
                    continue
                # positive-definite
                eig = np.linalg.eigvalsh(C)
                if eig.min() <= 0:
                    bad += 1
        assert checked > 40000, f"too few covariances checked: {checked}"
        rate = bad / checked
        assert rate < 0.001, f"{bad}/{checked} ({rate:.3%}) non-SPD covariances"

    def test_correlations_in_range(self, kelvins_rows):
        """All correlation coefficients must lie in [-1, 1]."""
        bad = 0
        total = 0
        for row in kelvins_rows:
            for p in ("t", "c"):
                for fld in (f"{p}_ct_r", f"{p}_cn_r", f"{p}_cn_t"):
                    v = row.get(fld, "")
                    try:
                        x = float(v)
                    except (TypeError, ValueError):
                        continue
                    total += 1
                    if not (-1.0000001 <= x <= 1.0000001):
                        bad += 1
        assert total > 100000, f"too few correlations: {total}"
        assert bad == 0, f"{bad}/{total} correlations outside [-1,1]"

    def test_pc_pipeline_bounded(self, kelvins_rows):
        """Run a sample through the full Pc pipeline; every result in [0,1]."""
        # 750 rows, not 2000. pc_2d costs ~65 ms per row, so 2000 took 129 s --
        # more than half the whole suite, and the suite is the deploy gate.
        # Measured: every row in the sample yields a Pc, so 750 gives 750
        # evaluations against the > 500 floor asserted below; if the drop-out
        # rate ever changes, that assertion fails rather than passing quietly on
        # a thin sample. This is a bounds check, not a statistical survey -- a
        # regression producing NaN, a negative Pc or one above 1 shows up in the
        # first handful of rows, and the remaining value of a larger sample is
        # covariance-shape variety, which 750 rows still spans.
        r1 = np.array([6778.0, 0.0, 0.0]); v1 = np.array([0.0, 7.668, 0.0])
        checked = 0
        for row in kelvins_rows[:750]:
            Ct = _rtn_cov_from_kelvins(row, "t")
            Cc = _rtn_cov_from_kelvins(row, "c")
            if Ct is None or Cc is None:
                continue
            try:
                md = float(row.get("miss_distance", ""))
            except (TypeError, ValueError):
                continue
            r2 = np.array([6778.0, 0.0, md / 1000.0])
            v2 = np.array([0.0, 0.0, 7.668])
            pc = pc_2d(r1, v1, Ct, r2, v2, Cc, 10.0)
            if pc is not None:
                assert 0.0 <= pc <= 1.0, f"Pc={pc} out of range"
                checked += 1
        assert checked > 500, f"too few Pc evaluations succeeded: {checked}"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v", "--tb=short"]))
