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
