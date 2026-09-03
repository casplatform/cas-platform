# ADR 0002 — The document toolchain is not in this repository, and we are not rebuilding it yet

- **Status:** Accepted (records a gap; defers the fix)
- **Date:** 2026-09-03
- **Relates to:** Phase 9 documentation inventory

## Context

`static/docs/` holds 20 DOCX files. Five are linked from the portal's
Documentation page, one more is served behind auth, and the rest are archived.
They are the artefacts an outside evaluator would read: SRS, VCRM, Validation
Report, Operational Evidence, Operator User Guide.

Nothing in this repository produces them. There is no source text, no template,
no generation script, and no diagram pipeline. The output is the only thing we
have.

## What was searched, and what was found

Searched 2026-09-03, read-only:

| Where | Result |
|---|---|
| `/root`, `/home`, `/opt`, `/tmp`, `/srv`, `/usr/local` — any `.py`/`.js`/`.sh` mentioning docx generation | nothing |
| Whole filesystem for `w:document`, `officegen`, `python-docx`, `from docx import`, `require('docx')` | only this repo's own `tests/test_portal_doc_cards.py` and two unrelated `bs4` test files |
| `python-docx` installed in either venv or system Python | not installed anywhere |
| Node: every `package.json` outside `node_modules`; any `docx`/`officegen`/`docxtemplater` package | none — the Node projects on this host are Tribun, elarasim and kupam |
| npm cache and `_npx` entries | `create-vite`, `web-push`, `playwright`, `tsx` — no document library |
| `git log --diff-filter=D` for a deleted generator | only two archived DOCX files and the retired `deploy/prepared/` |

**Conclusion: the tool has never been on this machine.** It is not lost, not
deleted, not gitignored — it runs somewhere else.

## What the artefacts do tell us

The files carry a consistent fingerprint, which is evidence about the tool even
though the tool is absent:

- **`docProps/app.xml` is present but empty** — `<Properties/>` with no
  `Application` element. Word always writes `Application` and `AppVersion`.
  `docProps/custom.xml` is empty in the same way.
- **`dc:creator` = `CAS Platform`, `cp:lastModifiedBy` = `Un-named`,
  `cp:revision` = 1** across every generated file. A distinctive default, and
  identical everywhere.
- **The zip contains directory entries** (`_rels/`, `docProps/`, `word/`) as
  well as files — many libraries omit these.
- **Nine documents share one byte-identical `word/styles.xml`** (md5
  `9248b9f5`), so there is a template, and it is applied by a program rather
  than by copying a Word file.
- **Exactly one document was made by Word:** archived `CAS_SRS_v2.0.docx`
  carries `Application = Microsoft Office Word`, `AppVersion = 16.0000`. It is
  the exception that shows the rest are not.

These point to a programmatic generator run outside this host. They do not
identify which one, and this ADR deliberately does not guess: naming a library
on a fingerprint alone would put an unverified claim into the record, which is
the failure `docs/commit-message-errata.md` exists to correct.

## The second mechanism: in-place XML editing

Two files carry `word/document.xml.bak` inside the zip — `CAS_SRS_v4.1.docx` and
`CAS_SRS_v4.2.docx`, both stamped 2026-08-09, the backup 21 minutes before the
live part. Comparing them shows an ordinary content edit, not damage: v4.2 adds
58 paragraphs (`3.13 Data Health and Source Freshness`, `3.14 Orbital
Collision-Burden Index`, `A source shall be reported stale when…`) and removes
two dated sentences.

So the August SRS updates were made by editing `word/document.xml` in place,
inside the existing file, leaving a backup — not by re-running the generator.
`docProps/core.xml` is still stamped `2026-06-22` and `dc:title` still reads
"…Specification v4.0", because in-place editing never touched them.

**That is the real finding.** Updating a document today means hand-editing XML
in a zip. It works — the August edit is clean and the file is well-formed — but
it is the same shape as every "output without source" this month has closed:
`.deploy_version.json` written by hand, the visual-guide PDF living only on
production's disk, `deploy/prepared/` holding an already-applied unit. The
generator is the last one left.

## Decision

**Record the gap. Do not rebuild the toolchain now, and do not guess at it.**

Rebuilding means choosing a library, rebuilding the template from the existing
`styles.xml`, converting six documents' worth of content back into source form,
and reproducing the diagram pipeline — with no reference output to test against
except the DOCX files themselves. That is a project, not a task, and nothing
currently depends on it: the documents are correct, current enough, and the
August edit shows they can still be changed.

What follows from this decision:

- **DOCX content is out of reach for now.** Any documentation fix that requires
  changing a DOCX is blocked until either the tool is recovered or a new one is
  built. Fixes that touch markdown, the portal, or access control are not.
- **Ask before rebuilding.** Whoever ran the generator on 2026-06-22 and edited
  the XML on 2026-08-09 knows what it was. Recovering it costs one question;
  reconstructing it costs weeks.
- **If it is recovered, it belongs in this repository** — source text, template,
  script and diagram pipeline — under `tools/docgen/`, so that a document
  becomes a build product rather than an artefact nobody can reproduce.

## Consequences

- `docs/validation/*.md` can be corrected; `static/docs/*.docx` cannot, for now.
- The empty `docProps/app.xml` and the stale `dc:title` ("v4.0" on a v4.2 file)
  stay as they are. They are cosmetic and invisible to a reader of the rendered
  document.
- This ADR is the answer to "why is there no way to rebuild the SRS", and it
  should be updated — not replaced — when the tool is found.
