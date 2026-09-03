# Superseded documents

Kept, not deleted. Version history is part of the evidence in an ECSS-aligned
document set, and the reasoning in `docs/adr/0001-freeze-the-legacy-engine.md`
against removing things that are merely unused applies here too.

`superseded/` holds every release the portal no longer links. Six of them state
positioning this project has since retired and must not be quoted:

| Document | Problem |
|---|---|
| `CAS_SRS_v1.0.docx`, `CAS_SRS_v1.0_backup.docx` | Title and §2.3 call CAS a "Collision Avoidance System" |
| `CAS_API_Reference_v1.0.docx` | Title, and "programmatic access to collision avoidance data" |
| `CAS_Operator_User_Guide_v1.1.docx` | Instructs the operator to "plan a collision avoidance maneuver" |
| `CAS_TRL5_Operational_Evidence_v2.0.docx` | "collision avoidance system" |
| `CAS_Validation_Report_v1_0.docx`, `_v1_1.docx` | "§6.4 No ML Components", while XGBoost Layer 1 is live |

CAS provides conjunction **decision support**. It does not perform collision
avoidance, and the ML layer is deployed and gated — never "scoring".

These were anonymously downloadable until 2026-09-03: nginx serves `static/`
with `try_files`, so guessing the URL returned 200 even though the portal never
linked them. `location ^~ /docs/archive/ { return 404; }` closes that.

`tests/test_portal_doc_cards.py` fails if the portal ever links something from
here.
