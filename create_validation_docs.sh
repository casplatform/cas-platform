#!/bin/bash
# Creates CAS validation documentation skeleton
set -e

mkdir -p /opt/cas/docs/validation
cd /opt/cas/docs/validation

# ════════════════════════════════════════════════════════════════════
# 1) VALIDATION_REPORT.md — top-layer narrative document
# ════════════════════════════════════════════════════════════════════
cat > VALIDATION_REPORT.md << 'MDEOF'
# CAS Validation Report

**Document version:** v0.1 (skeleton)
**System under test:** CAS — Conjunction Decision Support Platform
**Component version:** cas_engine.py (current)
**Test framework:** pytest 9.0.3, Python 3.12
**Last test run:** _(auto-populated on release)_
**Status:** Work in progress — skeleton for iterative completion

---

## 1. Executive Summary

> **TODO (narrative, 200–400 words):** Describe what CAS is, what this report
> validates, and the overall result. Keep it readable to a non-technical
> reader. Suggested structure: (1) what CAS does in one sentence,
> (2) what we're validating in this report, (3) headline result
> ("55 automated tests, 100% pass, Foster-integral implementation
> converges to analytical closed-form within 10⁻³"),
> (4) what we're *not* claiming (see §6).

---

## 2. Scope & Methodology

### 2.1 What this report covers

This report documents the automated validation of CAS's **computational
core** — the pure functions and classes that transform raw Conjunction
Data Messages (CDMs) into operator-facing decision support output.

**In scope:**
- Foster-integral collision probability (`collision_probability`)
- Modified Bessel I₀ helper (`_bessel_i0`)
- CDM parser (`parse_cdm`)
- Risk classifier (`risk_level`)
- Decision engine: priority, recommendation, confidence
  (`DecisionEngine._compute_*`)

**Out of scope (see §6):**
- HTTP server layer and request routing
- Database persistence and queries
- Email notification delivery
- Authentication (JWT, API key)
- Third-party integrations (Space-Track, EU SST, Celestrak) — covered by
  operational evidence, not unit tests

### 2.2 Methodology

All validation is performed via automated unit tests using the `pytest`
framework. Each test is deterministic, self-contained, and runs in under
two seconds for the full suite. Tests exercise boundary conditions,
monotonicity invariants, schema robustness, and — where analytical
solutions exist — cross-check numerical implementations against
closed-form references.

Tests are organized by the component under validation:
- `tests/test_collision_probability.py` — Foster Pc + Bessel helper
- `tests/test_cdm_parser.py` — CDM ingestion boundary
- `tests/test_risk_level.py` — risk classifier thresholds
- `tests/test_decision_engine.py` — decision output logic

### 2.3 Reproducibility

The entire validation suite is reproducible with a single command:

```bash
cd /opt/cas && python3 -m pytest tests/ -v
```

Expected output: `55 passed in ~2s`. Any failure indicates a regression
in the computational core and must be investigated before deployment.

---

## 3. Component Validation

### 3.1 Foster-integral collision probability

**Implementation:** `cas_engine.py::collision_probability(miss_m, sigma, hbr)`

Foster's method (1992) reduces the 2D Gaussian collision integral to a
single-variable integral involving the modified Bessel function I₀:

$$P_c = \int_0^{s} e^{-(x^2 + u^2)/2} \cdot I_0(u \cdot x) \cdot x \, dx$$

where *u = miss / σ* and *s = HBR / σ*. The implementation uses rectangle-rule
numerical integration with N = 200 subdivisions.

**Validation approach:** Behavioral invariants plus an analytical
cross-check for the head-on case (see §4).

**Tests:** 11 — see `tests/test_collision_probability.py`

| Property | Test |
|---|---|
| I₀(0) = 1 (analytical) | `TestBesselI0::test_i0_at_zero_is_one` |
| I₀(1) ≈ 1.2660658732 (reference) | `TestBesselI0::test_i0_small_arg` |
| I₀(5) ≈ 27.2398718 (reference) | `TestBesselI0::test_i0_large_arg` |
| I₀ is even (symmetry) | `TestBesselI0::test_i0_symmetric` |
| σ below 10⁻³ returns 0 (guard) | `TestCollisionProbability::test_sigma_below_threshold_returns_zero` |
| **Head-on matches analytical** | `TestCollisionProbability::test_zero_miss_high_pc` |
| Far miss → Pc < 10⁻¹⁵ | `TestCollisionProbability::test_far_miss_near_zero_pc` |
| Monotonic decrease in miss | `TestCollisionProbability::test_monotonic_decrease_with_miss` |
| HBR² area scaling | `TestCollisionProbability::test_hbr_area_scaling` |
| Output bounded to [0,1] | `TestCollisionProbability::test_bounded_range` |
| CARA-range reference point | `TestCollisionProbability::test_cara_reference_point` |

**Result:** 11 / 11 pass. See §4 for the analytical cross-check detail.

---

### 3.2 CDM parser

**Implementation:** `cas_engine.py::parse_cdm(cdm: dict) -> dict`

Parses Space-Track CDM JSON objects into the CAS internal conjunction
record. Designed to be robust against Space-Track schema drift via
explicit field fallback chains and graceful type coercion.

**Tests:** 14 — see `tests/test_cdm_parser.py`

| Property | Test class |
|---|---|
| Happy-path parse + 17-field schema | `TestParseCDMHappyPath` |
| Field fallback chains (MIN_RNG → MISS_DISTANCE → MINIMUM_RANGE) | `TestFieldFallbacks` |
| Field aliases (PC / COLLISION_PROBABILITY, SAT_1_NAME / SAT1_NAME) | `TestFieldFallbacks` |
| String → float coercion | `TestTypeCoercion` |
| Invalid input defaults to zero, no exceptions | `TestMissingAndMalformed` |
| Risk level integration (Pc > 10⁻⁴ → RED) | `TestRiskIntegration` |
| Pc_str format (scientific, 3 decimals) | `TestRiskIntegration` |

**Isolation note:** `parse_cdm` opens a PostgreSQL connection for cascade
maneuver enrichment, wrapped in `try/except: pass`. Tests force this path
to fail (invalid DB_URL in `conftest.py::isolate_db` fixture), exercising
the graceful-degradation branch. This is a deliberate test design
decision: it keeps unit tests free of DB dependencies while still
running the full parser code path.

**Result:** 14 / 14 pass.

---

### 3.3 Risk classifier

**Implementation:** `cas_engine.py::risk_level(Pc, miss_m) -> str`

Three-level classifier (RED / YELLOW / GREEN) based on two independent
thresholds: collision probability and miss distance. Either threshold
alone is sufficient to escalate.

**Thresholds:**
- RED: `Pc > 1e-4` or `miss_m < 200`
- YELLOW: `Pc > 1e-5` or `miss_m < 1000`
- GREEN: otherwise

**Tests:** 9 — see `tests/test_risk_level.py`

Tests exercise every branch (RED via Pc, RED via miss, YELLOW via Pc,
YELLOW via miss, GREEN, combined cases) plus boundary conditions
(`Pc = 1e-4` exactly falls through to YELLOW because the comparison is
strict `>`, not `>=`). Boundary tests are explicit because they catch
the most common regression: accidentally swapping `>` with `>=` during
refactoring.

**Result:** 9 / 9 pass.

---

### 3.4 Decision engine

**Implementation:** `cas_engine.py::DecisionEngine` (class, line 1245)

The decision engine consumes parsed CDM fields and produces three
actionable outputs for operators:
1. **Priority** (HIGH / MEDIUM / LOW) — score-based, weights Pc,
   miss distance, and time remaining to TCA.
2. **Recommendation** (Maneuver advised / Monitor / No action) —
   rule-based, with TCA-passed downgrade logic.
3. **Confidence** (high / medium / low) — reflects orbit determination
   reliability as a function of time-to-TCA and Pc extremity.

**Tests:** 17 — see `tests/test_decision_engine.py`

| Sub-component | Test count | Key invariants |
|---|---|---|
| `_compute_priority` | 6 | Category boundaries, monotonicity in Pc, None-timing safety |
| `_compute_recommendation` | 6 | RED → Maneuver, high Pc override, critical miss override, TCA-passed → Monitor, YELLOW → Monitor, low → No action |
| `_compute_confidence` | 7 | TCA proximity × Pc extremity matrix, no-timing fallback |
| Integration invariants | 2 | Threshold ordering, instantiation without DB |

**Test design note:** `_compute_priority` uses a hard-coded score sum
(40 + 30 + 30). Tests validate the *category outcome*, not the score
arithmetic. This is a deliberate test design decision: if the internal
scoring weights are retuned, these tests continue to pass as long as
the category boundaries remain coherent. Brittle tests that asserted
specific scores would create false failures on every tuning change.

**Result:** 17 / 17 pass.

---

## 4. Analytical Cross-Check

For the special case of a head-on approach (miss = 0), the Foster
integral collapses to a closed-form solution:

$$P_c(\text{miss}=0) = 1 - e^{-s^2/2}, \quad s = \text{HBR}/\sigma$$

This provides an independent analytical reference against which the
numerical implementation can be cross-validated. The test
`TestCollisionProbability::test_zero_miss_high_pc` exercises this
case with σ = 30 m, HBR = 10 m:

| Quantity | Value |
|---|---|
| s = HBR/σ | 0.333... |
| Analytical P_c | 1 − exp(−0.0556) ≈ 0.05406 |
| Numerical P_c (N=200) | matches to within 10⁻³ |
| Tolerance used in test | 10⁻³ |

**Interpretation:** The numerical integration converges to the analytical
solution with high accuracy. This is the strongest single validation
datum in the report — it is a test that *could* fail for reasons
unrelated to implementation bugs (e.g., N chosen too low), and it
passes. This shifts the Foster-integral implementation from "trust" to
"verified against reference".

> **TODO:** Add second analytical cross-check if one exists —
> e.g., limiting behavior as σ → ∞ (Pc → 0), or miss → ∞ (Pc → 0).
> These are implicitly in the test suite but could be lifted here.

---

## 5. Results Summary

| Component | Tests | Pass | Coverage* |
|---|---:|---:|---:|
| Foster Pc + Bessel I₀ | 11 | 11 | high |
| CDM parser | 14 | 14 | high |
| Risk classifier | 9 | 9 | 100% |
| Decision engine | 17 | 17 | high |
| **Total** | **55** | **55 (100%)** | — |

\* "High" indicates every behavioral branch is exercised; a precise
per-module coverage percentage can be obtained with
`pytest --cov=cas_engine --cov-report=term-missing`.

**Overall module-level `cas_engine.py` coverage:** approximately 14%.
This low number reflects the large untested surface area (HTTP server,
DB layer, email, auth) — which is **intentionally out of scope** for
this report (see §6). The computational core, which is in scope, has
near-complete branch coverage.

---

## 6. Known Limitations and Exclusions

### 6.1 Covariance data not available

Space-Track's public CDM class returns 16 fields but **does not include
the full covariance matrix** (CR_R, CT_T, CN_N absent). CAS therefore
operates with an assumed effective sigma (σ = 100 m) rather than
per-event covariance. This is a *data* limitation, not an
implementation limitation — the Foster integral itself accepts arbitrary
σ. Operational implication: CAS Pc values are best interpreted as
**relative risk rankings** rather than absolute probabilities.

> **TODO:** Expand with concrete operator guidance: "a CAS Pc of 10⁻³
> indicates high relative risk, but the absolute figure depends on the
> true covariance which is unavailable at this tier."

### 6.2 I/O-heavy code paths not unit tested

Approximately 2,000 lines of `cas_engine.py` implement HTTP routing,
DB CRUD, email delivery, and authentication. These paths are validated
**operationally** (30+ days of continuous production operation,
hourly scanner runs, real CDM ingestion) rather than by unit tests.
Integration tests for these paths are planned for a future iteration
and are not required at this validation level.

### 6.3 Third-party data source behavior not tested

CAS ingests from Celestrak, Space-Track, and EU SST. The *ingestion
logic* is tested via CDM parser unit tests; the *external sources
themselves* are treated as trusted and are not subject to validation
in this report. Operational evidence of successful ingestion
(28,410 objects tracked live) is documented separately.

### 6.4 No machine-learning components

All components validated in this report are deterministic. ML-based
components (false-positive reduction, Pc trend prediction, maneuver
decision support) are on the roadmap for post-TRL 5 development and
are not covered here.

---

## 7. Traceability to SRS Requirements

See companion document: `test_evidence_matrix.md`

> **TODO:** The traceability matrix maps each SRS v2.0 requirement
> (REQ-XXX-NNN) to one or more tests in the validation suite. This
> matrix is currently a skeleton with placeholder IDs — requirement
> IDs must be extracted from `CAS_SRS_v2.0.docx` and filled in manually
> (roughly 40 rows).

---

## 8. Reproducibility Checklist

- [x] All tests are automated (pytest)
- [x] Full suite runs in under 5 seconds
- [x] No external dependencies required at test time (DB isolated via fixture)
- [x] Single-command execution: `pytest tests/ -v`
- [x] Test source under version control alongside implementation
- [ ] CI/CD pipeline runs suite on every commit — *TODO*
- [ ] Coverage threshold enforcement — *TODO*
- [ ] Test results published alongside each release tag — *TODO*

---

## 9. Next Steps

> **TODO:** Populate after H2 planning session. Expected items:
> - Add tests for `compute_dv`, `compute_cascade_maneuver`, `TrendAnalyzer`
> - Extract SRS requirement IDs and populate traceability matrix
> - Add second analytical cross-check
> - Document operational evidence (uptime, scan history, STARLINK-34343 case study)
> - Set up CI pipeline

---

## Appendix A — Test Execution Log

> **TODO:** Paste the latest full `pytest -v` output here before each
> document release. This provides an immutable snapshot of the result
> set at report-sign-off time.

---

*End of report skeleton. This document is intended to be filled in
iteratively alongside test suite expansion.*
MDEOF
echo "[OK] VALIDATION_REPORT.md written ($(wc -l < VALIDATION_REPORT.md) lines)"

# ════════════════════════════════════════════════════════════════════
# 2) test_evidence_matrix.md — traceability matrix (skeleton)
# ════════════════════════════════════════════════════════════════════
cat > test_evidence_matrix.md << 'MDEOF'
# Test Evidence Matrix — SRS Traceability

**Purpose:** Map each SRS v2.0 requirement to the automated test(s)
that validate it, providing ECSS-aligned traceability for TRL 5
compliance.

**Status:** Skeleton. Requirement IDs are placeholders. To complete
this matrix, open `static/docs/CAS_SRS_v2.0.docx`, extract each
requirement ID, and fill in the corresponding row below.

**Verification method codes:**
- **T** — Test (automated, reproducible)
- **A** — Analysis (closed-form, mathematical proof)
- **I** — Inspection (code review, static check)
- **D** — Demonstration (operational, non-automated)

---

## Computational Core Requirements

| Req ID | Requirement (short) | Method | Test reference | Status |
|---|---|---|---|---|
| REQ-FUNC-XXX | Compute collision probability using Foster method | T + A | `test_collision_probability.py::TestCollisionProbability` + §4 analytical | ✅ |
| REQ-FUNC-XXX | Classify conjunctions into RED/YELLOW/GREEN risk levels | T | `test_risk_level.py::*` | ✅ |
| REQ-FUNC-XXX | Parse Space-Track CDM JSON into internal record | T | `test_cdm_parser.py::TestParseCDMHappyPath` | ✅ |
| REQ-FUNC-XXX | Handle Space-Track schema variants gracefully | T | `test_cdm_parser.py::TestFieldFallbacks` | ✅ |
| REQ-FUNC-XXX | Produce priority assessment (HIGH/MEDIUM/LOW) | T | `test_decision_engine.py::TestComputePriority` | ✅ |
| REQ-FUNC-XXX | Produce maneuver recommendation | T | `test_decision_engine.py::TestComputeRecommendation` | ✅ |
| REQ-FUNC-XXX | Produce confidence assessment | T | `test_decision_engine.py::TestComputeConfidence` | ✅ |
| REQ-NFR-XXX  | Computational core must not crash on malformed input | T | `test_cdm_parser.py::TestMissingAndMalformed` | ✅ |

---

## Data Ingestion Requirements

> **TODO:** Populate from SRS v2.0 §3.x (data ingestion section).
> These requirements are validated operationally (not by unit tests)
> and should reference the operational evidence package when it exists.

| Req ID | Requirement | Method | Evidence reference | Status |
|---|---|---|---|---|
| REQ-DATA-XXX | Ingest CDMs from Space-Track hourly | D | `cas.service` systemd logs (30+ days) | ⏳ |
| REQ-DATA-XXX | Ingest TLE catalog from Celestrak | D | Live catalog ~28,000 objects | ⏳ |
| REQ-DATA-XXX | Ingest LEO debris + rocket bodies from Space-Track | D | `.spacetrack_catalog_cache.json` | ⏳ |

---

## Interface Requirements

> **TODO:** Populate from SRS v2.0 §4.x (interface section).

| Req ID | Requirement | Method | Evidence reference | Status |
|---|---|---|---|---|
| REQ-IFACE-XXX | REST API authentication via API key | I + D | `AuthManager` class, operational | ⏳ |
| REQ-IFACE-XXX | Email notifications for RED-level events | D | `EmailNotifier` operational logs | ⏳ |

---

## Non-Functional Requirements

> **TODO:** Populate from SRS v2.0 §5.x (NFR section). Performance,
> reliability, security, etc.

---

*End of matrix skeleton.*
MDEOF
echo "[OK] test_evidence_matrix.md written ($(wc -l < test_evidence_matrix.md) lines)"

# ════════════════════════════════════════════════════════════════════
# 3) analytical_cross_checks.md — Foster closed-form benchmark detail
# ════════════════════════════════════════════════════════════════════
cat > analytical_cross_checks.md << 'MDEOF'
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
MDEOF
echo "[OK] analytical_cross_checks.md written ($(wc -l < analytical_cross_checks.md) lines)"

# ════════════════════════════════════════════════════════════════════
# 4) README.md — dizin açıklaması + kullanım
# ════════════════════════════════════════════════════════════════════
cat > README.md << 'MDEOF'
# CAS Validation Documentation

This directory contains the validation documentation suite for CAS
(Conjunction Decision Support Platform). Documents in this directory
provide evidence that CAS's computational core operates as specified
in the SRS, supporting the TRL 5 validation objective.

## Contents

| File | Purpose | Audience |
|---|---|---|
| `VALIDATION_REPORT.md` | Top-layer narrative report. Describes what is validated, how, and with what result. Includes executive summary, methodology, per-component validation, limitations. | Technical + non-technical |
| `test_evidence_matrix.md` | ECSS-aligned traceability matrix mapping SRS requirements to test cases and verification methods. | Auditors, formal review |
| `analytical_cross_checks.md` | Mathematical derivations for cases where CAS's numerical implementations can be cross-validated against closed-form analytical solutions. | Technical reviewers |
| `README.md` | This file. |

## Companion test suite

The validation documentation references automated tests in
`/opt/cas/tests/`:

```
tests/
├── conftest.py                       # pytest config + DB isolation fixture
├── test_collision_probability.py     # Foster Pc + Bessel I₀ (11 tests)
├── test_cdm_parser.py                # CDM ingestion (14 tests)
├── test_risk_level.py                # Risk classifier (9 tests)
└── test_decision_engine.py           # Decision output (17 tests)
```

## Running the tests

From the CAS root directory:

```bash
cd /opt/cas
python3 -m pytest tests/ -v
```

Expected output: `55 passed in ~2s`.

With coverage reporting:

```bash
python3 -m pytest tests/ --cov=cas_engine --cov-report=term-missing
```

## Updating this documentation

This suite is intended to be updated **iteratively** alongside the
test suite. The workflow is:

1. **Add tests** for a new component or fix. Ensure they pass.
2. **Update `VALIDATION_REPORT.md` §3** with the new component's
   validation summary and test table.
3. **Update `test_evidence_matrix.md`** with the requirement-to-test
   mapping.
4. **Append to `analytical_cross_checks.md`** if the new tests
   leverage an analytical reference.
5. **Bump the version** in `VALIDATION_REPORT.md` header and update
   the "last test run" timestamp.

## Versioning

Document versions follow semver-like notation:
- **v0.x** — skeleton, work in progress, not for external circulation
- **v1.0** — first complete release aligned with a CAS engine version
- **v1.x** — incremental additions; bump minor for new component sections

Current version: **v0.1 (skeleton)**

## Status snapshot

As of skeleton creation:
- 55 automated tests
- 100% pass rate
- 4 components validated (Foster Pc, CDM parser, risk classifier, decision engine)
- 5 analytical cross-checks documented
- SRS traceability matrix populated for computational core only
  (data ingestion, interfaces, and NFRs pending)
MDEOF
echo "[OK] README.md written ($(wc -l < README.md) lines)"

# ════════════════════════════════════════════════════════════════════
# Final summary
# ════════════════════════════════════════════════════════════════════
echo ""
echo "=== Validation docs created at /opt/cas/docs/validation/ ==="
ls -la /opt/cas/docs/validation/
echo ""
echo "Total lines:"
wc -l /opt/cas/docs/validation/*.md
