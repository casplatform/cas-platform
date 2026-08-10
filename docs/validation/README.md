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

Expected output: `84 passed in ~2s`.

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

Current version: **v1.0 (April 2026)**

## Status snapshot

As of skeleton creation:
- 84 automated tests
- 100% pass rate
- 7 components validated (Foster Pc, CDM parser, risk classifier, decision engine)
- 5 analytical cross-checks documented
- SRS traceability matrix populated for computational core only
  (data ingestion, interfaces, and NFRs pending)
