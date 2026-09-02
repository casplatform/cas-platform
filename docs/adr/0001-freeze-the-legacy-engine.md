# ADR 0001 — Freeze the legacy engine; do not migrate it

- **Status:** Accepted
- **Date:** 2026-09-02
- **Supersedes:** nothing. This is the first ADR.
- **Decides:** what happens to `cas_engine.py` under the Strangler Fig migration.

If you are reading this because you are wondering why this system still runs two
HTTP services, the short answer is: **because measurement said moving was the
more expensive option, and the dependency direction already points the right
way, so the choice stays open at no cost.** The long answer follows.

---

## Context

`CLAUDE.md` and `README.md` both describe a Strangler Fig migration in progress:
`cas_engine.py` (a single `BaseHTTPRequestHandler`, port 8765) is legacy, new
work goes into `cas_api/` (FastAPI, port 8766), and the rule is "no new features
in the engine".

What did not exist was a plan for the migration itself. The pattern names a
direction but not an end state, and nobody had written down whether the engine
was supposed to shrink to nothing, shrink partway, or simply stop growing. The
absence showed: the engine changed nineteen times in the nineteen days after the
initial commit, and each change had to be argued from first principles because
there was no standing decision to appeal to.

Phase 8 was a read-only survey to answer that. Everything below is measured, and
the commands are in the report that produced this ADR.

## Measurements

**The engine's real load, 30 days of nginx access log (2026-08-03 → 09-02).**
The raw log is 166,381 lines and misleading: 128,437 of those are internet
background noise (`/wp-admin`, `/.env`, `/xmlrpc.php`). A first pass also
wrongly credited the engine with 35,551 hits on `GET /` — in nginx `/` is
`try_files`, static, and never reaches port 8765. Counting only what nginx
actually forwards to `cas_engine` (six location blocks: `/api/`, `/stats/`,
`/catalog/`, `/tle/`, `/history`, `/health`):

| | |
|---|---|
| Requests forwarded to the engine | **904 in 30 days** (~30/day) |
| Of those, our own monitoring (smoke, curl) | ~285 (~31%) |
| Real user traffic | **~620, i.e. ~20/day** |
| Defined (verb, path) pairs | 82 |
| Pairs that received at least one request | 37 |
| Pairs receiving traffic only from our monitoring | ~8 |
| **Pairs seeing real user traffic** | **~15** |
| Pairs receiving zero requests | 45 |

**Size.** 6,996 lines. Routing is an `if self.path == ...` chain inside `do_GET`
and `do_POST` — 73 branches, 2,230 lines (32%). The other 68% is infrastructure
and domain logic: `CASHandler` 2,185, `AdminManager` 553, `DecisionEngine` 484,
`WatchlistManager` 436, `TrendAnalyzer` 403, `AuthManager` 325.

**The change profile — the measurement that decided this.** Every one of the
nineteen commits that touched `cas_engine.py` after the initial commit was
reliability work, not features:

```
1bccd94 alarm on a source that never ran      observability
1f927f3 let a source go stale                 observability
2c882cc judge our own work                    observability
d9c3ecb fail closed when auth cannot check    security
a10268f resolve paths from CAS_HOME           isolation
e6a7df1 close two unauthenticated writes      security
677b978 close isolation gaps                  isolation
fb51723 take DDL out of request paths         schema
143d181 port and bind from the environment    isolation/security
83585ea remove the CelesTrak dependency       rate limits
...
```

None was blocked or slowed by the file being a monolith. The Strangler pattern
exists to relieve a codebase that hurts to work in; nineteen consecutive changes
landed without that pain appearing.

**The rule held, but was never tested.** In the same nineteen days `cas_api/`
also received no new features — the only files added were `deploy_info.py` and
`paths.py`, both infrastructure. So "no new features in the engine" was obeyed
because the question never came up, which is weaker evidence than it looks.

**Dependency direction — the reason freezing costs nothing.** The engine imports
from `cas_api` (`core.data_health`, `core.deploy_info`). `cas_api` imports
nothing from the engine, deliberately;
`cas_api/core/tier_features.py` records why:

> *"We do NOT import cas_engine here because importing it triggers engine
> module-level side effects (watchlist scanner, email manager) and expects
> DB_URL in os.environ."*

`cas_api` is already the lower layer and the engine the upper one. A future
migration would move code downhill, along a gradient that already exists.

**The engine is also a library and a scheduler, not only a server.** Twenty-four
sites do `import cas_engine`: eleven test modules and four cron/analysis scripts
(`decision_scanner.py`, `backfill_scan.py`, `ocbi_backfill_proto.py`,
`tle_archive_download.py`) pulling `compute_dv`, `collision_probability`,
`parse_cdm`, `risk_level`, `TrendAnalyzer`, `DecisionEngine`, `TierConfig`.
`WatchlistManager`'s background scanner is a thread inside the engine process,
and `fetch_cdm.py` drives the entire daily CDM intake through
`POST /spacetrack/auto`. Retiring the HTTP surface would not retire the engine.

## Decision

**Freeze `cas_engine.py`.** No migration project. New work continues to go into
`cas_api/`, and the engine keeps receiving reliability, security and correctness
fixes as it has been.

Two exceptions, both narrow and both justified independently of the freeze:

1. **The two health endpoints move** — `GET /health/sources` (15 lines) and
   `GET /health/detailed` (6 lines). Both are already thin HTTP wrappers around
   logic that lives in `cas_api.core.data_health`. Moving them costs the engine
   no logic at all; it deletes a shell.
2. **`notification-prefs` is de-duplicated** — the engine serves
   `GET/POST /api/notification-prefs` and FastAPI serves
   `GET/PUT /api/v2/notifications/prefs`, and the portal calls both. This is a
   defect regardless of the freeze: while it is ambiguous which one wins, the
   two will eventually diverge.

Neither exception is a first step of a migration. If the freeze is later
revisited, they are useful; if it is not, they still stand on their own.

## Why the alternatives were rejected

### Full migration — everything to FastAPI, the engine is shut down

Rejected on cost against benefit at this maturity. It would touch roughly 4,000
of the engine's 6,996 lines: rewriting the 2,185-line `CASHandler`, moving or
wrapping four manager classes (1,798 lines), relocating the background scanner
thread, repointing `fetch_cdm.py`, updating ~25 call sites in the portal JS,
removing six nginx location blocks, and retargeting eleven test modules. Weeks
of work for one developer, on a live system tracking 126 satellites with three
CDM intakes a day.

The benefits it buys — a single runtime, an API contract, generated
documentation, parallel development — are all benefits nobody is asking for yet.
The system is at TRL 5 with no external pilot users (README, "Known
constraints").

### Partial migration — move a subset, the engine shrinks but stays

Rejected as the worst of the three, not the compromise it appears to be. Every
move requires simultaneous changes on four surfaces — engine, FastAPI, nginx,
portal JS — and those are not atomic, because nginx is applied by hand and the
other three ship through `scripts/deploy.sh`.

A migration that stalls halfway leaves two auth patterns, two database access
patterns and two sets of documentation, kept in sync by one developer's memory.
`tier_features.py` is the small-scale proof: a hand-maintained mirror carrying
`Last synced: 2026-07-08`. Phase 6's conclusion was that an unwatched mechanism
is a maintenance burden; a half-migration is that conclusion at scale.

### Delete the 45 endpoints that receive no traffic

This is the most attractive conclusion in the survey and the most wrong, so it
gets its own section.

**Zero requests does not mean dead.** `POST /watchlist/remove`,
`POST /auth/register` and `POST /admin/user/update` all received zero requests
in the window — and all three are called by `static/portal.html`. Nobody pressed
those buttons in 30 days because there are no external pilot users; the six
accounts are internal. The measurement says "unused during this window", which
is a different claim from "unreachable".

Two further reasons the inference does not hold:

- **The window is 19 days of logs, not 30.** nginx's rotation covers 19 days, so
  anything used weekly or monthly may simply not appear.
- **Deletion is irreversible in a way this project has spent a month avoiding.**
  Phases 0–7 built rollback paths, deploy gates, health reporting and config
  drift detection. Opening the next phase with an irreversible cleanup, justified
  by an incomplete measurement, would be the opposite of that investment.

If dead code is to be removed later, the evidence needed is different from a
traffic log: a static check that no portal JS, no cron script and no test
references the route, plus a longer observation window.

### Let `cas_api` import the engine

Rejected, and worth naming because it is the subtle one. It looks like the fix
for duplication — `TierConfig` would stop being a mirror. But it inverts the
dependency direction that makes this whole decision cheap, and it pulls the
engine's module-level side effects (the watchlist scanner thread, the email
manager) into the FastAPI process. A mirror is a maintenance cost; an inverted
dependency is an architectural mistake that cannot be walked back.

## Consequences

- The system runs two HTTP services for the foreseeable future. That is a
  deliberate state, not drift.
- `tier_features.py` stays a hand-maintained mirror. It carries a sync date;
  keep updating it.
- The engine keeps receiving security and reliability fixes. "Frozen" means no
  new features, not untouched.
- The migration option stays open at zero carrying cost, because the dependency
  direction already points from the engine into `cas_api`.

## Reopening triggers

This decision is scoped to today's conditions. Revisit it when any of these
happens — and revisit it deliberately, by writing ADR 0002, not by starting to
move endpoints:

1. **The first external pilot user.** An API contract, versioning and generated
   documentation stop being hypothetical benefits.
2. **A second developer.** A single 2,185-line `CASHandler` blocks parallel work
   in a way one person never feels.
3. **The third time someone wants to write a new feature into the engine.** The
   rule has held so far without being tested — no new features went anywhere in
   the measured window. A third breach would be evidence that the rule has
   failed in practice, and a rule nobody follows is worse than an honest change
   of direction.

## Method note

Every number here came from a read-only survey: nginx access logs, `git log`,
AST parsing of `cas_engine.py`, the live OpenAPI document, and `SELECT` queries.
No file was modified while producing it. Two errors were caught and corrected
during the survey — crediting the engine with static `GET /` traffic, and
matching log paths against the engine's route table rather than against nginx's
forwarding rules. Both inflated the engine's apparent load; the corrected
numbers are the ones above.
