# CAS — Conjunction Decision Support Platform

CAS ingests Conjunction Data Messages (CDMs) and public catalog data for LEO/VLEO
satellite operators, screens them, and produces ranked, explainable decision
support: which conjunctions deserve attention, why, and what the maneuver trade
space looks like.

**What it does not do.** CAS does not perform collision avoidance. It does not
command, plan or execute maneuvers, and it has no link to any spacecraft. Every
output is advisory — human-in-the-loop by design: the operator decides and
executes. The maneuver endpoints sweep a lead-time × Δv trade space offline;
nothing is actuated.

Two further distinctions the codebase is careful about, and this document keeps:

- **Pc consistency, not accuracy.** The validation suite shows the Foster-integral
  implementation agrees with closed-form analytical references and with itself
  across inputs. It does not claim the resulting number is the true probability of
  collision — that would need covariance CAS does not receive (see below).
- **ML is deployed and gated, not scoring.** The canonical Layer-1 XGBoost model
  is live in `cas_api/services/ml_inference.py` and runs on every request. It sits
  behind a feature-coverage gate — `COVERAGE_THRESHOLD = 0.70` in
  `ml/src/canonical_scoring.py`, measured over 107 canonical features. Public
  Space-Track CDMs are 16-field and fill far too few of them, so the gate returns
  `tier="UNAVAILABLE"` and the deterministic Pc funnel decides. Operator-tier CDMs
  carrying covariance would pass the gate with no code change. "ML is deployed and
  gated" is defensible; "ML is scoring our conjunctions" is not.

Operational notes for working *inside* this repository — restart timings, rate
limits already paid for in outages, verification habits — live in
[`CLAUDE.md`](CLAUDE.md) (Turkish). This README is the entry point; CLAUDE.md is
the field manual. They deliberately do not repeat each other.

---

## Architecture

A Strangler migration, **deliberately frozen** — see
[ADR 0001](docs/adr/0001-freeze-the-legacy-engine.md).

```
Cloudflare Tunnel ──► nginx (127.0.0.1:80) ──┬─► /api/v2/*  ─► cas-api  :8766   FastAPI (new work)
                                             ├─► /api/*     ─► cas      :8765   legacy engine
                                             └─► /          ─► static/          landing, portal, catalog
                                                                 │
                                                          PostgreSQL 16  (casdb)
```

- **`cas_engine.py`** — the legacy service (`BaseHTTPRequestHandler`, port 8765).
  It is the original monolith and still serves most of `/api/*`. **No new features
  go here**; it is imported by tests and cron scripts for its pure functions.
  nginx strips the `/api` prefix before forwarding.
- **`cas_api/`** — FastAPI on uvicorn, port 8766, two workers in production.
  All new endpoints are `/api/v2/*`. Interactive docs at `/api/v2/docs`.
- nginx listens on loopback only; the public edge is a Cloudflare Tunnel.
- Schema changes go through Alembic (`migrations/`). A runtime `CREATE TABLE`
  inside a request handler is what once produced two conflicting definitions of
  the same table — do not reintroduce that.

Both services read the same `.env` and resolve every path from `CAS_HOME`, which
is what lets a second instance (staging) run from another directory on the same
host. `tests/test_env_robustness.py` enforces this: no `/opt/cas` string literal
in the root `*.py` scripts, `cas_api/**` or `ml/src/**`, the sole exception being
an `os.environ.get("CAS_HOME", "/opt/cas")` default on the same line.

---

## Data sources and their quotas

The quotas below are not theoretical. Two of them were learned by being cut off.

| Source | Used for | Cadence | Constraint |
|---|---|---|---|
| **Space-Track** (CDM) | conjunction messages | `fetch_cdm.py` at 00:00 / 08:00 / 16:00 | **3 requests/day, no headroom.** The account was suspended in July 2026 for running hourly. A fourth call in a calendar day is a real risk; a failed run waits for the next slot — never retry. |
| **Space-Track** (GP / SATCAT) | catalog, TLEs, object identity | `refresh_catalog_cache.py` daily 01:30; `sync_satcat.py`, `sync_directory_satcat.py` weekly | Hourly limit, a few queries a day used — this is where the headroom is. |
| **EU SST** | fragmentation (FG) and re-entry (RE) events | `eusst_sync.py --service all` every 6h | OAuth client credentials, read-only Service Provision API. |
| **NOAA SWPC** | Kp, F10.7, GOES X-ray, alerts | `space_weather_sync.py` hourly at :15 | Public, no key. Feeds the VLEO drag/density work. |
| **ESA DISCOS** | object mass and geometry | `sync_discos_mass.py` weekly | Bearer token (`DISCOS_TOKEN`). SATCAT gives identity; DISCOS gives mass. |
| **TheSpaceDevs** (`ll.thespacedevs.com` 2.2.0) | launch schedule | `refresh_launch_cache.py` every 4h | Public API. |

**CelesTrak is not a data source, on purpose.** This server has been firewalled by
CelesTrak since 2026-05-24, after a per-satellite hourly loop produced roughly
3,000 requests/day. Every automated CelesTrak call was removed. What remains is a
single `/tle/` proxy in `cas_engine.py`, behind a circuit breaker that makes **one**
attempt, never retries, and opens for six hours on any policy-stop response
(301/302/403/404/429/5xx), falling back to cache. Do not add CelesTrak calls, and
do not add a retry — ignoring the stop signal is what got this IP blocked.

Covariance is the structural gap: public Space-Track CDMs carry none. That single
fact is why Pc is a screening number, why the maneuver trade space is indexed on
miss distance rather than post-maneuver Pc, and why the ML coverage gate does not
open today.

---

## Setup on a fresh machine

Assumes Ubuntu 24.04, Python 3.12, PostgreSQL 16, nginx.

**1. Tree and user.** Services run as the unprivileged `cas` system user; create
it if absent, then clone into `/opt/cas`.

**2. Per-instance virtualenv.** Each instance owns its interpreter — never share
one, and never install into the system python:

```bash
cd /opt/cas
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt -c constraints.txt
```

`-c constraints.txt` is mandatory, not decoration: on a clean venv without it, 22
transitive packages resolve to versions production does not run. Optional extras,
deliberately kept in separate files:

```bash
.venv/bin/python -m pip install -r requirements-dev.txt -c constraints.txt  # pytest, requests
.venv/bin/python -m pip install -r requirements-ops.txt -c constraints.txt  # alembic — ops only; no service imports it
.venv/bin/python -m pip install -r requirements-ml.txt                      # model training only
```

**3. `.env`** at the instance root, mode `600`, owned by `cas`. Key names only —
values come from the credential store and never from this repository:

```
ST_IDENTITY    ST_PASSWORD        # Space-Track, primary account
ST_IDENTITY_2  ST_PASSWORD_2      # Space-Track, secondary account
DB_URL                            # PostgreSQL DSN for this instance
AUTH_SECRET                       # JWT signing key — no default; the service refuses to start without it
DISCOS_TOKEN                      # ESA DISCOS bearer token
EUSST_CLIENT_ID   EUSST_CLIENT_SECRET
EUSST_USERNAME    EUSST_PASSWORD
EUSST_TOKEN_URL   EUSST_API_BASE
SMTP_HOST  SMTP_PORT  SMTP_USER  SMTP_PASS  SMTP_FROM
ALERT_EMAILS                      # operator notification recipients
CAS_HOME                          # this instance's root — what keeps it out of /opt/cas
ENVIRONMENT                       # production | staging
```

`AUTH_SECRET` having no default is deliberate: it used to fall back to a random
value, which logged every user out on each restart.

**4. Schema.** Alembic builds it. `schema.sql` is a historical artifact, not the
source of truth. `migrations/env.py` reads the URL from the instance `.env` via
`CAS_HOME` and, unlike the services, has **no `CAS_HOME` default** — it writes DDL,
so it refuses to guess which instance it is pointed at:

```bash
CAS_HOME=/opt/cas .venv/bin/python -m alembic upgrade head
```

**5. systemd.** `cas.service` (engine) and `cas-api.service` (FastAPI), both
`User=cas`, both `EnvironmentFile=<root>/.env`, both starting from
`<root>/.venv/bin/python`. The deploy script's first gate refuses to run if a
unit's `ExecStart` is not the venv interpreter. A staging instance additionally
sets `CAS_HOME`, `CAS_PORT`, `CAS_BIND=127.0.0.1` and `ENVIRONMENT=staging`;
its units are intentionally `static`, so they do not come up on boot.

**6. nginx.** `deploy/nginx-cas.conf` is the reference copy of what is deployed to
`/etc/nginx/sites-enabled/`. See `deploy/README.md` for how to apply a change
without breaking the config.

**7. cron.** The root crontab drives every sync: `fetch_cdm.py`, `eusst_sync.py`,
`space_weather_sync.py`, `decision_scanner.py`, the catalog / SATCAT / DISCOS /
launch refreshers, `insurance_watch_cron.py`, `scripts/backup_db.sh`,
`watchdog.sh` and `scripts/run_smoke_cron.sh`. Each script's docstring carries its
actual crontab line. Data-fetch entries run from the venv interpreter;
`backup_db.sh` and the smoke cron deliberately stay on the system python — they
are black-box clients, and pytest does not belong in a runtime venv.

**8. Ownership.** The services read as `cas`. After any root-run install, git
write, or pytest run:

```bash
chown -R cas:cas /opt/cas
```

---

## Running the tests

From the instance root, using **that instance's** interpreter:

```bash
.venv/bin/python -m pytest -q                       # unit + integration, ~2m30s
.venv/bin/python -m pytest -q --ignore=tests/smoke   # what CI runs
```

As of 2026-08-25, `pytest --collect-only` reports **436 tests** excluding smoke and
**29** in `tests/smoke`. Treat that as a dated snapshot, not a contract — re-run
`--collect-only` rather than trusting this line.

**Which database: never production.** `tests/integration/conftest.py` derives a
test database from the instance's `DB_URL` (`casdb` / `casdb_staging` →
**`casdb_test`**) or takes an explicit `TEST_DB_URL`, and aborts the entire session
via `pytest.exit` if the name does not end in `_test`. The guard exists because
these tests commit — `AUTH.register()` and friends — and clean up afterwards with
cascade fixtures. Unit tests touch no database at all: `tests/conftest.py` points
`DB_URL` at an unreachable sentinel so DB-optional code paths take their fallback.

**Why smoke is separate.** `tests/smoke/` asserts on the state of a *running
deployment* — live endpoints, "the catalog holds more than N objects", "the sync
has run recently". That is not a property of the code in a commit, so CI excludes
it; on a fresh checkout every one of those tests would fail for reasons that say
nothing about the change. It runs against staging and production, where the
question means something, and daily at 04:00 through `scripts/run_smoke_cron.sh`.
Details in `tests/smoke/README.md` and `tests/integration/README.md`;
`run_tests.sh [unit|integration|smoke|all]` is a grouped runner.

CI (`.github/workflows/ci.yml`) runs on every push and pull request, as three
independent jobs so one failure cannot hide another:

- **`test`** — a PostgreSQL 16 service container, the schema built by
  `alembic upgrade head` (running the migrations *is* itself the schema test),
  then the suite.
- **`secrets`** — `gitleaks` over the full history, `fetch-depth: 0`, because a
  diff-scoped scan cannot see a secret committed once and never touched again.
- **`audit`** — `pip-audit` against every pinned version in `constraints.txt`,
  blocking on a finding rather than warning. It is deliberately *not* wired into
  `scripts/deploy.sh`: a CVE published against a version nobody touched would
  otherwise turn yesterday's green deploy red and stand between a hotfix and
  production.

Both scanning jobs check that they actually covered something before reporting
clean — an empty result read as a clean result is a failure this repository has
hit more than once. The workflow needs no secrets, and it should stay that way.

---

## Deploy

Production is never edited by hand. It is a checkout of a commit that already ran
in staging and passed the suite there.

```
edit in /opt/cas_staging → restart both staging services → pytest →
commit + git push origin main → /opt/cas/scripts/deploy.sh
```

Restarting staging before testing is not optional: a running service serves the
code it started with, and a stale staging process has twice sent a bug hunt to the
wrong place. `CLAUDE.md` carries the exact restart sequence and its measured
timings.

`scripts/deploy.sh` runs **13 numbered gates** (counted from the script's own step
labels). In summary they verify both interpreters and that the units' *effective*
`ExecStart` is the venv → require a clean production working tree → fetch
`origin/main` and print the incoming diff → require staging to be on exactly that
commit, with a clean tree → restart staging on it and wait for health → require
`casdb_test` to be at production's Alembic revision, so the suite tests the schema
production runs → run the full suite in staging against `casdb_test` → confirm
with the operator → dump the database → sync the production venv from the target's
`requirements.txt` and `constraints.txt`, then prove the packages import → move
production to the commit and record the rollback point in the same breath →
restart and health-check three endpoints (engine `/health`, `/api/v2/health`,
`portal.html`).

The rollback point is recorded when production actually moves, not before: an
entry written earlier would name a commit production had never left if the deploy
then aborted, and the top of the stack would equal the running `HEAD` — which
makes `--rollback 1` a no-op at the worst possible moment. For the same reason a
successful rollback pops the entry it landed on.

If that final health check fails, **the script rolls itself back** — code and venv
both — restarts, and re-checks. Manual rollback:

```bash
/opt/cas/scripts/deploy.sh --history      # recorded deploy points, newest first
/opt/cas/scripts/deploy.sh --rollback     # undo the last deploy
/opt/cas/scripts/deploy.sh --rollback 2   # two deploys back
```

The one-shot `deploy_*.sh`, `deploy_*.py` and `setup_*` scripts in the repository
root are **retired**. They wrote straight into production with no gate, no test and
no rollback; each now exits 2 if invoked. They are kept as a record of what was
once deployed. `scripts/deploy.sh` is the only way to ship.

Staging is browsable over an SSH tunnel — see `tools/staging-tunnel.command`.

---

## Directory map

| Path | Contents |
|---|---|
| `cas_engine.py` | Legacy monolith, port 8765. Read it and import it; do not extend it. |
| `cas_api/` | FastAPI service — `api/v2/` routers, `services/` business logic, `core/` (config, DB pool, auth, paths, data health), `schemas/` CCSDS CDM models, `mappers/` source → canonical CDM. |
| `ml/` | `src/` feature extraction, canonical scoring, training; `models/` trained XGBoost artifacts and metrics; `datasets/` (gitignored, large). |
| `migrations/` | Alembic. Baseline revision plus hand-written migrations; no ORM, so no autogenerate. |
| `tests/` | Unit tests at the top level; `integration/` (real DB, commits, cleans up); `smoke/` (live deployment, GET-only). |
| `scripts/` | `deploy.sh`, `backup_db.sh`, `restore_db.sh`, `run_smoke_cron.sh`. |
| `deploy/` | Reference copies of configuration that lives outside the repo (nginx, the staging API unit). Records what **is** deployed, never what is pending — `diff` them against the live files before trusting either. |
| `docs/validation/` | Validation report, ECSS-aligned evidence matrix, analytical cross-checks. Versioned documents — check the header date before quoting them. |
| `docs/adr/` | Architecture decision records. Start with [ADR 0001](docs/adr/0001-freeze-the-legacy-engine.md): why there are still two HTTP services. |
| `docs/commit-message-errata.md` | Corrections of record: commit messages on `main` that describe something the diff does not do, and survey findings later retracted. Nothing is rewritten; the correction is filed next to the claim. |
| `static/` | Landing, portal, catalog, insurance and legal pages, served directly by nginx. |
| `specs/` | Field-mapping CSVs: canonical, CCSDS, TRACSS. |
| root `*.py` | Cron-driven sync, scan and enrichment scripts. Each docstring carries its crontab line. |
| root `deploy_*`, `setup_*`, `add_*`, `fix_*` | Retired one-shot patches, guarded to refuse execution. |

---

## Known constraints

Stated plainly, so the next person does not have to discover them.

- **No external pilot users.** The system is live and continuously fed, but
  operator validation to date is internal. Positioning language should reflect it.
- **Bus factor 1.** A single developer holds the context. This README and
  `CLAUDE.md` are the mitigation, and they are only as good as their last update.
- **Two commit messages on `main` are wrong.** Both were written by summarising a
  work report rather than reading the diff, and both describe work that was not in
  the commit. The history is shared and production deploys from it, so it is not
  being rewritten — the corrections are in
  [`docs/commit-message-errata.md`](docs/commit-message-errata.md). Check the code
  before quoting a commit message as evidence.
- **Staging is not isolated.** It runs on the same host as production, against the
  same PostgreSQL server, and shares the same Space-Track quota. The separation is
  logical — separate tree, database, ports, units and venv — not physical. Staging
  has no cron by design, so its data files drift from production's and are
  refreshed by hand.
- **No covariance in public CDMs.** This is the ceiling on Pc fidelity, on the
  maneuver trade space, and on ML scoring. Everything downstream is screening-grade
  until operator-tier CDMs arrive.
- **The legacy engine is large and untyped, and it is staying.** `cas_engine.py`
  is one enormous `BaseHTTPRequestHandler` file. It is frozen rather than being
  migrated: no new features go into it, security and reliability fixes still do,
  and there is no migration project. That is a measured decision, not drift —
  [ADR 0001](docs/adr/0001-freeze-the-legacy-engine.md) carries the numbers, the
  rejected alternatives (including the tempting "delete the 45 endpoints nobody
  calls", which is wrong because the portal calls them), and the conditions under
  which the decision should be reopened. Expect to read the old engine, and expect
  new work to land beside it rather than inside it.
