# Commit message errata

Last updated: 2026-08-26.

Two commit messages on `main` state things that are not true of the diffs they
describe. Both were written by summarising a work report instead of reading the
diff, and both read as plausible because the rest of the same message is
accurate.

**The history is not being rewritten.** `main` is shared and production deploys
from it (`scripts/deploy.sh` resets `/opt/cas` to a commit on this branch), so a
rebase would invalidate every deployed SHA and every reference to one. The
messages stay wrong; this file is the correction of record.

**How to find it.** Each entry is keyed by full and short SHA, so `git log`
sends you here the moment you search the repository for a commit id:

    grep -rn 6e816d8 .

If you are about to quote a commit message as evidence for how something works,
check the code first. That is the failure this file documents.

---

## 6e816d857f45727f56953033bcb6f66167b1b5e0 (`6e816d8`, 2026-08-24)

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

## 48b0f5a169526f4d0008098781bc5beacb46fd32 (`48b0f5a`, 2026-08-18)

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
