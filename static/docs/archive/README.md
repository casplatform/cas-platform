# Superseded documents

Kept, not deleted. Version history is part of the evidence in an ECSS-aligned
document set, and the reasoning in `docs/adr/0001-freeze-the-legacy-engine.md`
against removing things that are merely unused applies here too.

Everything the portal no longer links lives in **`superseded/`** — one flat
directory, no other layout. There used to be three: files directly under
`archive/`, a dated `archive/20260501/`, and `archive/superseded/`. The dated
directory held two DOCX files that were byte-identical to their copies in
`superseded/` (verified by md5), so it was a duplicate layer rather than a
second generation, and it is gone.

## Do not quote these

Six of them state positioning this project has since retired:

| Document | Problem |
|---|---|
| `CAS_SRS_v1.0.docx`, `CAS_SRS_v1.0_backup.docx` | Title and §2.3 call CAS a "Collision Avoidance System" (the two files are byte-identical) |
| `CAS_API_Reference_v1.0.docx` | Title, and "programmatic access to collision avoidance data" |
| `CAS_Operator_User_Guide_v1.1.docx` | Instructs the operator to "plan a collision avoidance maneuver" |
| `CAS_TRL5_Operational_Evidence_v2.0.docx` | "collision avoidance system" |
| `CAS_Validation_Report_v1_0.docx`, `_v1_1.docx` | "§6.4 No ML Components", while XGBoost Layer 1 is live |

CAS provides conjunction **decision support**. It does not perform collision
avoidance, and the ML layer is deployed and gated — never "scoring".

## `CAS_Operator_Portal_Visual_Guide_archived_20260501.pdf`

The previous edition of the visual walkthrough (1,435,232 bytes, 17 pages). The
current one is `../CAS_Operator_Portal_Visual_Guide.pdf` (1,149,920 bytes, also
17 pages); the two differ in content, not in length.

**The date in the name is when it was archived, not when it was written.** It
comes from the old `archive/20260501/` directory. Both PDFs carry an mtime of
2026-05-01 and neither has a readable version marker inside — the text is
encoded with embedded font subsets — so there is no honest basis for a version
number. `_archived_` says exactly what the date means and nothing more.

This file reached production by hand and was never in any repository until
2026-09-03, which is what deploy gate 2 caught: it refuses to run while
production's tree is dirty, and an untracked file is dirt.

## Access

`nginx` returns 404 for everything under `/docs/archive/`:

```nginx
location ^~ /docs/archive/ { return 404; }
```

Before that, `static/` was served with `try_files`, so guessing
`/docs/CAS_SRS_v1.0.docx` returned 200 even though the portal never linked it.
404 rather than 403: a 403 would confirm the file is there.

`tests/test_portal_doc_cards.py` fails if the portal ever links something from
this directory.
