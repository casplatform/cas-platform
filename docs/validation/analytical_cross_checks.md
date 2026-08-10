# Analytical Cross-Checks

This document collects the closed-form references against which CAS's
numerical implementations are cross-validated. Each entry describes a
case where an analytical solution exists, the value it predicts, and
the corresponding automated test that exercises the cross-check.

---

## 1. Foster Integral — Head-on Case (miss = 0)

### Setup

The general Foster-integral collision probability is:

$$P_c = \int_0^{s} e^{-(x^2 + u^2)/2} \cdot I_0(u \cdot x) \cdot x \, dx$$

where *u = miss / σ* and *s = HBR / σ*. For the special case miss = 0:
- *u = 0*
- *I₀(0 · x) = I₀(0) = 1* for all *x*
- The integrand reduces to *exp(−x²/2) · x*

### Derivation

$$P_c(\text{miss}=0) = \int_0^{s} x \cdot e^{-x^2/2} \, dx$$

Substituting *v = x²/2*, *dv = x dx*:

$$P_c = \int_0^{s^2/2} e^{-v} \, dv = 1 - e^{-s^2/2}$$

### Numerical values

For σ = 30 m, HBR = 10 m (a typical close-approach scenario):
- *s = 10/30 = 0.333...*
- *s²/2 = 0.0556*
- *exp(−0.0556) = 0.94599*
- **Analytical P_c = 0.05406**

### Implementation

`cas_engine.py::collision_probability(miss_m, sigma, hbr)` uses
rectangle-rule integration with N = 200 subdivisions.

### Test

`tests/test_collision_probability.py::TestCollisionProbability::test_zero_miss_high_pc`

```python
def test_zero_miss_high_pc(self):
    pc = collision_probability(miss_m=0, sigma=30, hbr=10)
    expected = 1 - math.exp(-0.5 * (10/30)**2)
    assert abs(pc - expected) < 1e-3
```

### Result

The numerical implementation converges to the analytical value within
10⁻³ absolute tolerance. This confirms correct integrand evaluation,
correct integration limits, and sufficient N for the tested parameter
range.

### Interpretation

This is the strongest single validation datum in the CAS test suite
because it is a **positive construction**: an independent analytical
solution exists, it predicts a specific number, and the numerical
implementation reproduces that number to high accuracy. A failure here
would indicate a real implementation bug, not a test-definition
artifact.

---

## 2. Limiting Behavior — Far-Field Miss

### Analytical expectation

As miss → ∞ with σ and HBR fixed, *u → ∞*, and the integrand
*exp(−(x² + u²)/2) · I₀(ux) · x* decays super-exponentially because
the *exp(−u²/2)* prefactor dominates. Formally:

$$\lim_{\text{miss} \to \infty} P_c = 0$$

### Test

`tests/test_collision_probability.py::TestCollisionProbability::test_far_miss_near_zero_pc`
asserts `P_c < 10⁻¹⁵` for miss = 10,000 m, σ = 50 m, HBR = 10 m.

### Result

Satisfied. The rectangle rule, combined with the `exp(exponent)` guard
clause (`if exponent < -700: continue`), correctly produces numerical
zero for extreme miss distances without underflow artifacts.

---

## 3. Monotonicity — Pc Decreases with Miss

### Analytical expectation

For fixed σ and HBR, *P_c* must be a strictly decreasing function of
miss distance. This is not a closed-form test but a **structural
invariant** that any correct Foster implementation must satisfy.

### Test

`tests/test_collision_probability.py::TestCollisionProbability::test_monotonic_decrease_with_miss`

### Result

Satisfied across miss ∈ {10, 50, 100, 200, 500} m.

---

## 4. HBR Scaling — Area Ratio

### Analytical expectation

For small *HBR/σ*, the Pc scales approximately as the **area** of the
hard-body region (π · HBR²). Doubling HBR should therefore approximately
quadruple P_c (ratio ≈ 4).

### Test

`tests/test_collision_probability.py::TestCollisionProbability::test_hbr_area_scaling`

Asserts the ratio P_c(HBR=10) / P_c(HBR=5) falls in [3.5, 4.5] for
miss = 100, σ = 50.

### Result

Satisfied. Actual ratio measured in-test; tolerance accounts for
finite-N integration error and second-order HBR terms.

---

## 5. I₀ Reference Values

The modified Bessel function of the first kind, order 0, is a published
special function with well-tabulated reference values. CAS implements
it via the Abramowitz & Stegun polynomial approximation.

| *x* | Reference *I₀(x)* | Test |
|---:|---:|---|
| 0 | 1.0 (exact) | `test_i0_at_zero_is_one` |
| 1 | 1.2660658732 | `test_i0_small_arg` |
| 5 | 27.2398718 | `test_i0_large_arg` |

Symmetry *I₀(−x) = I₀(x)* is additionally verified by `test_i0_symmetric`.

### Result

All values match the Abramowitz & Stegun tabulation to within the
tolerance specified in each test.

---

## Candidate Additions

> **TODO:** Consider adding the following analytical cross-checks in
> future iterations:
> - **Risk level thresholds**: verify each threshold produces the
>   correct classification at *exact*-threshold, *just-above*, and
>   *just-below* for each of the six possible transitions. (Partially
>   covered by `TestRiskLevelBoundaries`.)
> - **Decision priority score symmetry**: enumerate all 27 combinations
>   of (Pc category, miss category, time category) and verify the
>   score-to-category mapping.
> - **NASA CARA reference cases**: if published Pc benchmark scenarios
>   from the CARA project can be located, add them as regression
>   fixtures.

---

*End of analytical cross-checks.*
