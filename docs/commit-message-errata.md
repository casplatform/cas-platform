# Commit message errata

Last updated: 2026-09-03.

Corrections of record for claims this project published and later found to be
wrong. Two kinds live here so far: **commit messages on `main`**, and **findings
reported in a survey**. They share the mechanism that matters — the original is
not rewritten, the correction is filed next to it.

**Why the filename still says "commit message".** The scope is wider than the
name. Renaming it to `errata.md` was considered and rejected: commit `e6efdb6`
names this exact path in its message body, and a commit message cannot be
fixed — which is the premise this whole file rests on. Renaming would create,
in the very act of tidying, a published reference to a path that no longer
exists. A slightly narrow filename costs one paragraph of explanation; a dead
path inside an immutable message costs a reader's time forever. One mechanism,
one file: a second errata document would drift from this one the way two
implementations of the same endpoint did in August.

**The history is not being rewritten.** `main` is shared and production deploys
from it (`scripts/deploy.sh` resets `/opt/cas` to a commit on this branch), so a
rebase would invalidate every deployed SHA and every reference to one. The
messages stay wrong; this file is the correction of record.

---

## Contents

- [Commit messages](#commit-messages) — 6e816d8, 48b0f5a
- [Survey findings](#survey-findings) — Phase 9 inventory, finding Y-5

---

## Commit messages

**How to find it.** Each entry is keyed by full and short SHA, so `git log`
sends you here the moment you search the repository for a commit id:

    grep -rn 6e816d8 .

If you are about to quote a commit message as evidence for how something works,
check the code first. That is the failure this file documents.

---

### 6e816d857f45727f56953033bcb6f66167b1b5e0 (`6e816d8`, 2026-08-24)

*"Run both instances from their own virtualenv; add secret and audit gates"*

**What the message says.** The subject promises an "audit gate". The body says
"CI gains two jobs beside the suite", and a full paragraph describes a pip-audit
job: it "runs against constraints.txt and blocks on a finding rather than
warning", with the reasoning for blocking over warning.

**What is actually in the diff.** One job beside the suite: `secrets`
(gitleaks). At that commit `.github/workflows/ci.yml` defines `test` and
`secrets` and nothing else, and the string `pip-audit` does not appear anywhere
in the tree:

    git show 6e816d8:.github/workflows/ci.yml | grep -nE '^  [a-z-]+:'
    git grep -n pip-audit 6e816d8

The paragraph also ends with "The output is redacted in CI", which is a property
of the gitleaks step (`--redact`) that was pulled into the wrong paragraph.
pip-audit has no redaction flag and needs none: its output is package names and
advisory ids.

**What is true.** The six dependency upgrades in that commit are real, and so is
their effect — re-measured 2026-08-26 with pip-audit 2.10.1 against
`git show 6e816d8^:constraints.txt`: 33 unique advisories across pillow,
pydantic-settings, pygments, pyjwt, starlette and urllib3, none of which survive
against the tip. Nothing enforced that in CI until the `audit` job was written,
on 2026-08-26. The design the message describes is the design that was then
built, which is why the claim survived a read-through: it was a description of
intent written in the past tense.

### 48b0f5a169526f4d0008098781bc5beacb46fd32 (`48b0f5a`, 2026-08-18)

*"Make the restore script report failure, and prove it restores"*

**What the message says.** "`--single-transaction` was considered and rejected
on evidence rather than preference", because `pg_dump --clean --if-exists` emits
`DROP ... IF EXISTS` for objects absent from a fresh target and "under a single
transaction those NOTICEs abort the whole restore".

**What is actually in the diff.** The same commit *adds* `--single-transaction`.
It is on the restore pipeline in `scripts/restore_db.sh` (line 287 at the time of
writing) and the comment block above it argues for it — all-or-nothing, so a
failed restore leaves the database as it was rather than half-dropped.

**The stated reason is wrong too.** A `NOTICE` is not an error; it does not abort
a transaction, with or without `ON_ERROR_STOP=1`. The counter-evidence is in the
commit's own measurements, recorded in that comment block: 19:32 on 2026-08-18,
without `--single-transaction` into an empty target, 135s and zero errors; 19:36
with it into a full target, 150s and zero errors; 19:42, a deliberately failing
dump rolled back cleanly and left `users` at its original 7 rows. The message
inverted the conclusion of a measurement it was summarising.

---

## Survey findings

### Phase 9 documentation inventory (2026-09-02), finding **Y-5** — RETRACTED

**What was claimed.** The inventory reported that all six documents the portal
offers carry raw XML leaked into their readable text, one paragraph each, worded
identically:

> `</w:pBdr><w:spacing w:after="0"/><w:jc w:val="center"/></w:pPr><w:r><w:rPr><w:rFonts w:ascii="Consolas"…`

It concluded that the identical wording across six files pointed at an escaping
bug in a shared generator, and ranked the finding as ağırlık-1 — visible to an
outside reader, damaging to the document set's credibility.

**Why it was wrong.** The finding was an artefact of the tool that looked for
it. The scan matched text runs with `<w:t[^>]*>`, and that expression also
matches `<w:top w:val="single" .../>` — the top border of a paragraph, inside
`<w:pBdr>`. Once `[^>]*` swallowed the `op w:val=...` part, everything from
there to the next real `</w:t>` was returned as "text". The reported leak *is*
that intervening markup.

The corrected expression, `<w:t(?:\s[^>]*)?>`, returns what the paragraph
actually contains: `casplatform.com` — the cover-page footer, Consolas 9pt,
colour `#0097B2`, centred under a rule. Visible, ordinary, intended.

**What is true.** Re-audited on 2026-09-03 with a real XML parser
(`ElementTree`, iterating `w:t` nodes) across all 20 documents including the
archive: every file is well-formed, and of more than 9,000 text nodes, **zero**
contain markup. Nothing needs repairing, and no generator bug is implicated.

The "identical across six files" pattern was false too, and in an instructive
way: the same scan reported *no* leak in several documents. Not because those
were clean, but because their cover pages have no `w:pBdr` element for the
broken expression to trip over. The apparent signature of a shared tool was the
presence or absence of a shared template element.

**The lesson, now in CLAUDE.md.** The existing rule warned against reading an
empty or filtered result as a match. This was the mirror image — noise read as a
finding — and the same remedy covers both: ask a parser, not a pattern, and
verify the measuring instrument before believing what it measures.

Other Phase 9 findings stand: the portal cards contradicted the files they
linked (fixed), the visual guide was published but untracked (fixed), and six
archived documents carry retired positioning language (access closed).
