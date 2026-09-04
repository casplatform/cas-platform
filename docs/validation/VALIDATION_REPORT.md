# CAS Validation Report — SUPERSEDED DRAFT

> **Do not quote this document.** It is an unfinished 2026-04 draft, kept for
> its structure and its reasoning. The current validation report is
> **`static/docs/CAS_Validation_Report_v2.0.docx`** (CAS-VAL-002, June 2026),
> served from the portal's Documentation page behind authentication. Where the
> two disagree, the DOCX is authoritative.
>
> This file was emitted by `create_validation_docs.sh` — a retired one-shot
> script that describes its own output as a skeleton — and has not been edited
> since. Eight `TODO` markers remain, including the executive summary itself.
> It is left in place rather than deleted because the section structure and the
> limitations reasoning are still useful, and because a document that was wrong
> is better corrected in place than quietly removed
> (`docs/commit-message-errata.md`).

**Document version:** v1.0 draft (April 2026), superseded 2026-09-04
**System under test:** CAS — Conjunction Decision Support Platform
**Test framework:** pytest, Python 3.12
**Status:** Incomplete draft — see the TODO markers below

**Test counts are deliberately not stated here.** This header used to claim
"84 automated tests" while §1 quoted 55 and the suite had grown past 500; a
count written into prose is a count that goes stale. To measure the suite now:

```bash
.venv/bin/python -m pytest --collect-only -q | tail -1
```

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
- `tests/test_collision_probability.py` — Foster Pc + Bessel helper (11 tests)
- `tests/test_cdm_parser.py` — CDM ingestion boundary (14 tests)
- `tests/test_risk_level.py` — risk classifier thresholds (9 tests)
- `tests/test_decision_engine.py` — decision output logic (17 tests)
- `tests/test_compute_dv.py` — ΔV solver + trend forecast (16 tests)
- `tests/test_rank_debris.py` — debris ranking pure functions (13 tests)

### 2.3 Reproducibility

The entire validation suite is reproducible with a single command:

```bash
cd /opt/cas && python3 -m pytest tests/ -v
```

Expected output: `84 passed in ~2s`. Any failure indicates a regression
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

### 3.5 ΔV Computation

**Implementation:** `cas_engine.py::compute_dv(miss_m, sigma, lead_s, target_Pc)`

Binary search solver that computes the minimum velocity change (ΔV) needed
to reduce collision probability below a target threshold. The algorithm
iterates 40 times (converging to ~10⁻¹² precision) by adjusting miss
distance via `new_miss = miss_m + dv × lead_s × 0.5`.

**Tests:** 6 — see `tests/test_compute_dv.py::TestComputeDv`

Key validations: monotonicity (farther miss → less ΔV, shorter lead → more ΔV),
result verification (computed ΔV actually achieves target Pc), precision (4 decimal
places), and target sensitivity (stricter target → more ΔV).

**Result:** 6 / 6 pass.

---

### 3.6 Trend Forecast

**Implementation:** `cas_engine.py::TrendAnalyzer._simple_forecast(current_pc, slope_per_hour)`

Linear extrapolation of collision probability over 12h/24h/48h/72h horizons,
with risk classification (RED/YELLOW/GREEN) and confidence rating
(high/medium/low decreasing with forecast horizon).

**Tests:** 10 — see `tests/test_compute_dv.py::TestSimpleForecast` + `TestEmptyForecast`

Key validations: risk direction detection (escalating/de-escalating/stable),
Pc clamping to [0, 1], confidence degradation with horizon, four forecast
horizons present, empty forecast defaults.

**Result:** 10 / 10 pass.

---

### 3.7 Debris Ranking

**Implementation:** `rank_debris.py::compute_rankings(cdms, altitudes)`

Pure function that processes CDM records, classifies debris by name pattern,
computes per-debris threat scores (counterparty count × 1000 + cumulative Pc × 10⁶),
assigns altitude bands (500-600 km, 1000-1200 km), and produces ranked lists.

**Tests:** 13 — see `tests/test_rank_debris.py`

Key validations: debris classification heuristic, empty input handling,
threat score ranking order, altitude band assignment, CDM deduplication,
active-vs-active exclusion.

**Result:** 13 / 13 pass.

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
| ΔV computation | 6 | 6 | 100% |
| Trend forecast | 10 | 10 | 100% |
| Debris ranking | 13 | 13 | high |
| **Total** | **84** | **84 (100%)** | — |

\* "High" indicates every behavioral branch is exercised; a precise
per-module coverage percentage can be obtained with
`pytest --cov=cas_engine --cov-report=term-missing`.

**Overall module-level `cas_engine.py` coverage:** approximately 13%. Note: the engine grew from 2533 to 2742 statements due to new authentication and decision endpoints added during the validation period. The computational core coverage remains high.
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

### 6.4 Machine-learning components are out of scope *for this report*

**Corrected 2026-09-04.** This section previously read "No machine-learning
components — ML-based components … are on the roadmap for post-TRL 5
development". That was true when it was written in April 2026 and is not true
now: the canonical Layer-1 XGBoost model is live in
`cas_api/services/ml_inference.py` and runs on every scoring request.

What remains true is the narrower statement: **every component validated *in
this report* is deterministic.** The ML layer is not validated here. It is
covered by `CAS_Validation_Report_v2.0.docx` §ML Layer 1, which reports the
held-out discrimination figure.

Two things about the ML layer belong in any honest summary of it:

- It sits behind a feature-coverage gate (`COVERAGE_THRESHOLD = 0.70` in
  `ml/src/canonical_scoring.py`, over 107 canonical features). Public
  Space-Track CDMs are 16-field and fill far too few, so the gate returns
  `tier="UNAVAILABLE"` and the deterministic Pc funnel decides.
- Therefore "ML is deployed and gated" is defensible; "ML is scoring our
  conjunctions" is not. Operator-tier CDMs carrying covariance would pass the
  gate with no code change.

---

## 7. Traceability to SRS Requirements

**This pointer is broken, and knowing that is more useful than following it.**
`test_evidence_matrix.md` in this directory is an unfilled skeleton: placeholder
IDs, seventeen TODO markers, and a reference to SRS v2.0 — four versions behind
the current SRS v4.2, which is itself archived under
`static/docs/archive/superseded/`.

The traceability that exists is in **`static/docs/CAS_VCRM_v2.2.docx`**
(CAS-VCRM-002), which maps every SRS v4.2 requirement to its verification
evidence and reports 115 of 115 verified. Use that.

---

## 8. Reproducibility Checklist

- [x] All tests are automated (pytest)
- [x] Test source under version control alongside implementation
- [x] CI/CD pipeline runs the suite on every push and pull request —
      `.github/workflows/ci.yml`, added 2026-08 (three jobs: suite, gitleaks,
      pip-audit)
- [x] The suite is also a deploy gate — `scripts/deploy.sh` refuses to ship a
      commit whose tests fail
- [ ] Coverage threshold enforcement — still open
- [ ] Test results published alongside each release tag — still open

~~Full suite runs in under 5 seconds~~ — no longer true and no longer
desirable: the suite now includes integration tests against a real PostgreSQL
schema built by Alembic, and runs in roughly two minutes.

~~Single-command execution: `pytest tests/ -v`~~ — use the instance's own
interpreter, `.venv/bin/python -m pytest -q`. The system Python and the venvs
resolve six of the pinned packages to different versions, so a run from the
wrong interpreter reports on a tree nothing deploys.

~~No external dependencies required at test time~~ — unit tests need none, but
the integration tier requires the `casdb_test` database, and it takes an
exclusive `flock` for the duration of a run so two suites cannot corrupt each
other's results.

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
