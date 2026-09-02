#!/usr/bin/env bash
#
# CAS deploy — GitHub main -> production.
#
# The rule this enforces: production is never edited by hand. It is a checkout
# of a commit that already ran in staging and passed the suite there. Before
# this script existed, changes were made directly in /opt/cas and a bad patch
# took the live system down -- three times on 2026-08-16 alone.
#
# Usage:
#   deploy.sh              interactive, asks before touching production
#   deploy.sh --yes        no prompt (for CI later; still runs every gate)
#   deploy.sh --rollback   return to the commit before the last deploy
#   deploy.sh --rollback 2 two deploys back, and so on
#   deploy.sh --history    show the recorded deploy points
#
# Never run `git checkout <branch>` in /opt/cas. It swaps the files the running
# services have open, with no restart and no health check -- the failure mode is
# a half-updated tree serving live traffic. Deploys move HEAD through this
# script; nothing else should move it.
#
set -uo pipefail

PROD=/opt/cas
STAGING=/opt/cas_staging
STATE=/root/.cas_deploy_state
# Each instance runs its own interpreter. The system python3 is shared by
# production, staging and the production crontab, so a dependency change there
# lands on all three at once and cannot be tested in one before the others --
# which is the drift requirements.txt and constraints.txt exist to describe and
# could not, on their own, enforce. Naming the interpreters here means every
# gate below runs against the environment the services actually use.
PROD_PY="$PROD/.venv/bin/python"
STAGING_PY="$STAGING/.venv/bin/python"
# The two cron scripts stay on the system python3, deliberately -- if anyone
# proposes "move everything to the venv", this is why they are the exception.
#
# scripts/run_smoke_cron.sh runs `python3 -m pytest tests/smoke/`, and pytest is
# not in the production venv and should not be. That venv is built from
# requirements.txt: it is what the services import while serving traffic, and a
# test framework installed there would be a package production carries and never
# imports. The smoke suite reaches the services over HTTP and psycopg2 -- it
# imports pytest, requests and psycopg2 for itself, never cas_api -- so which
# interpreter it runs on does not change what it is testing. The consequence to
# be aware of, not to fix: once production serves from the venv, the smoke cron
# is exercising venv-pinned services with system-python client libraries, and
# the two sets of versions drift apart from here on. That is the correct side of
# the line for a black-box check.
#
# scripts/backup_db.sh shells out to python3 only for its best-effort
# data_health report, which does import cas_api/core and needs psycopg2 from
# whichever interpreter runs it. It is already written to survive that call
# failing (a health-tracking problem must never fail a good backup), and pg_dump
# -- the part that matters -- is not python at all.
#
# The boundary is what the process is for: serving traffic runs from the venv
# and is version-pinned by this script; cron-side backup and smoke checking run
# from the system python and are not.
# Derived from staging's .env so the password comes from the same place the
# services get it. conftest would otherwise derive casdb_staging_test from
# staging's DB_URL -- a database that does not exist.
TEST_DB=$(sed -n 's/^DB_URL=//p' /opt/cas_staging/.env | tr -d '"'"'"'"'"'"'"' \
          | sed 's#/casdb_staging#/casdb_test#; s#/casdb$#/casdb_test#')
LOG=/var/log/cas/deploy.log

AUTO_YES=0; ROLLBACK=0; ROLLBACK_N=1; SHOW_HISTORY=0
while [ $# -gt 0 ]; do
  case "$1" in
    --yes|-y)    AUTO_YES=1 ;;
    --history)   SHOW_HISTORY=1 ;;
    --rollback)
      ROLLBACK=1
      # optional depth: --rollback 2 goes two deploys back
      case "${2:-}" in ''|*[!0-9]*) ;; *) ROLLBACK_N=$2; shift ;; esac ;;
    *) echo "unknown argument: $1"; exit 2 ;;
  esac
  shift
done

RED=$'\e[31m'; GRN=$'\e[32m'; YLW=$'\e[33m'; OFF=$'\e[0m'
step() { printf "\n${YLW}==> %s${OFF}\n" "$*"; }
ok()   { printf "    ${GRN}ok${OFF}  %s\n" "$*"; }
die()  { printf "\n${RED}ABORT: %s${OFF}\n" "$*"; log "ABORT: $*"; exit 1; }
log()  { mkdir -p "$(dirname "$LOG")"; printf '[%s] %s\n' "$(date -u '+%F %T UTC')" "$*" >> "$LOG"; }

health_check() {
  # Three probes. The engine and the API can each come up while the other is
  # broken, and nginx can serve neither -- checking one would miss two thirds
  # of the ways a deploy goes wrong.
  local fail=0
  curl -fsS --max-time 15 http://localhost:8765/health >/dev/null 2>&1 \
    && ok "engine /health" || { printf "    ${RED}FAIL${OFF} engine /health\n"; fail=1; }
  curl -fsS --max-time 15 -H "Host: casplatform.com" http://127.0.0.1/api/v2/health >/dev/null 2>&1 \
    && ok "api /api/v2/health" || { printf "    ${RED}FAIL${OFF} api /api/v2/health\n"; fail=1; }
  local code
  code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 15 -H "Host: casplatform.com" http://127.0.0.1/portal.html)
  [ "$code" = "200" ] && ok "portal.html 200" || { printf "    ${RED}FAIL${OFF} portal.html %s\n" "$code"; fail=1; }
  return $fail
}

# Wait for a service to actually answer, instead of guessing how long it takes.
#
# Startup time is not a constant. cas-api runs two uvicorn workers and each one
# loads XGBoost and a SHAP explainer for itself: cold, that took 29s on
# 2026-08-18 (started 14:19:55, "Application startup complete" 14:20:24). The
# same restart minutes later, with the tree still in page cache, finished in
# ~10s, and staging's single worker is ready in ~15s. The old `sleep 25` was
# tuned to one point in that range and rejected a healthy build by 4 seconds --
# health_check failed and the deploy rolled itself back over nothing.
#
# Polling costs nothing when the service is fast and waits when it is slow. The
# ceiling stays generous (90s) because the cost of being wrong is asymmetric: a
# few extra seconds against an unnecessary automatic rollback of a good commit.
#
# `systemctl is-active` is not the signal -- it reports active as soon as the
# unit's process exists, while the workers are still importing. Only the
# endpoint knows.
wait_for_health() {
  local label=$1 url=$2 timeout=${3:-90}
  local start elapsed
  start=$(date +%s)
  while :; do
    if curl -fsS --max-time 5 "$url" >/dev/null 2>&1; then
      elapsed=$(( $(date +%s) - start ))
      ok "$label ready in ${elapsed}s"; log "$label ready in ${elapsed}s"
      return 0
    fi
    # Measured against the wall clock, not a loop counter: one iteration is
    # sleep 3 plus up to 5s of curl timeout, so counting loops would have made
    # "90" mean anywhere between 34 and 90 real seconds.
    elapsed=$(( $(date +%s) - start ))
    [ "$elapsed" -ge "$timeout" ] && break
    sleep 3
  done
  printf "    ${RED}FAIL${OFF} %s not ready after %ss\n" "$label" "$timeout"
  log "$label not ready after ${timeout}s"
  return 1
}

restart_services() {
  # stop -> sleep -> start, not restart: the engine binds 8765 and a plain
  # restart has raced its own shutdown before.
  #
  # Returns non-zero if either service never came up. The callers run
  # health_check() next, which reports and handles the failure -- this return
  # value exists so the reason a wait ended is recorded either way.
  local rc=0
  systemctl stop cas; sleep 3; systemctl start cas
  wait_for_health "engine" "http://localhost:8765/health" 90 || rc=1
  systemctl restart cas-api
  # Probed on its own port rather than through nginx: what is being waited for
  # here is the uvicorn workers. health_check() covers the nginx path after.
  wait_for_health "api" "http://127.0.0.1:8766/api/v2/health" 90 || rc=1
  return $rc
}

restart_staging() {
  # Same shape as restart_services, against the staging instance: engine on
  # 8775, api on 8776. Only used by the pre-deploy gate; the rollback path
  # never calls it.
  local rc=0
  systemctl stop cas-staging; sleep 3; systemctl start cas-staging
  wait_for_health "staging engine" "http://127.0.0.1:8775/health" 90 || rc=1
  systemctl restart cas-api-staging
  wait_for_health "staging api" "http://127.0.0.1:8776/api/v2/health" 90 || rc=1
  return $rc
}

sync_prod_venv() {
  # Bring $PROD/.venv to whatever $PROD's tree now asks for. Called after every
  # `git reset --hard` that moves production backwards. The forward path does
  # its own install in gate 11 instead, from staging's copies, because there the
  # venv has to reach the target state while production's code is still
  # untouched.
  #
  # Reads $PROD's own requirements.txt and constraints.txt, and therefore must
  # run *after* the reset: what is wanted is the environment of the commit being
  # restored, and until the reset lands those two files still describe the
  # commit being run away from.
  #
  # Before the restart, not after. Gate 11 pulls the venv forward; gate 13 and
  # --rollback put the code back but left the environment on the new versions,
  # so a rolled-back production ran old code against new dependencies -- the
  # exact split this script exists to prevent, arriving in the one moment
  # nobody has attention to spare for it. "Get the service up first, fix the
  # environment after" does not avoid that: it restarts the old code onto the
  # new dependency set and then needs a second restart anyway, so it buys one
  # more broken window rather than a shorter one, and it makes the health check
  # a verdict on a state production is not going to stay in.
  #
  # The added time is proportional to the risk it removes. A rollback that does
  # not cross a dependency change is a no-op install -- 3.4s, measured
  # 2026-08-24 -- and the case that costs 30-60s is precisely the rollback where
  # skipping the sync would leave the two halves mismatched. Next to
  # restart_services (~40s of stop, sleep and two health waits) it is not the
  # part of a rollback worth shortening.
  #
  # Returns non-zero on failure; both callers deliberately carry on. Rationale
  # at the call sites.
  local rc=0
  step "Syncing the production venv to $(git -C "$PROD" rev-parse --short HEAD)"
  # No venv at all is not a failure here. Gate 1 refuses to *deploy* into that
  # state, but --rollback runs before gate 1 and has to keep working on a
  # production that has not been migrated yet: there the system python is the
  # environment, this script never moved it, and there is nothing to put back.
  # A .venv that exists without an interpreter is a different thing -- that one
  # is broken and gets reported.
  if [ ! -d "$PROD/.venv" ]; then
    ok "no $PROD/.venv -- production is still on the system python, nothing to sync"
    log "venv resync: skipped, no $PROD/.venv"
    return 0
  fi
  if [ ! -x "$PROD_PY" ]; then
    printf "    ${RED}FAIL${OFF} no interpreter at %s\n" "$PROD_PY"
    log "venv resync: no interpreter at $PROD_PY"
    return 1
  fi
  if [ ! -f "$PROD/requirements.txt" ] || [ ! -f "$PROD/constraints.txt" ]; then
    printf "    ${RED}FAIL${OFF} requirements.txt or constraints.txt missing from %s\n" "$PROD"
    log "venv resync: requirements.txt or constraints.txt missing from $PROD"
    return 1
  fi
  "$PROD_PY" -m pip install -q \
    -r "$PROD/requirements.txt" -c "$PROD/constraints.txt" || rc=$?
  # Unconditional and before the exit check, as in gate 11: pip runs as root
  # and writes root-owned files into a tree the services read as cas, and a
  # partial install still leaves some of them behind.
  chown -R cas:cas "$PROD/.venv"
  if [ "$rc" -ne 0 ]; then
    printf "    ${RED}FAIL${OFF} pip install into %s/.venv failed (exit %s)\n" "$PROD" "$rc"
    log "venv resync FAILED (pip exit $rc)"
    return 1
  fi
  ok "venv matches $(git -C "$PROD" rev-parse --short HEAD)"
  return 0
}

write_deploy_marker() {
  # Record which commit production is now serving, for /health to report.
  #
  # Called from every path that moves production's HEAD -- the deploy and both
  # rollbacks. A marker written only on the way forward would keep naming the
  # commit that was rolled back FROM, which is worse than no marker: it would be
  # confidently wrong at the one moment anyone reads it.
  #
  # Written after `git reset --hard`, never before: it describes what is on
  # disk, and a reset that fails must not leave a marker claiming otherwise.
  #
  # $PROD/.deploy_version.json is gitignored. It must be, or gate 2 would find
  # production's tree dirty on the next deploy and refuse to run -- this script
  # would have broken itself.
  local _sha=$1
  local _tmp="$PROD/.deploy_version.json.tmp"
  printf '{"commit": "%s", "deployed_at": "%s", "ref": "%s"}\n' \
    "$_sha" "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" "${2:-origin/main}" > "$_tmp" \
    && mv "$_tmp" "$PROD/.deploy_version.json" \
    && chown cas:cas "$PROD/.deploy_version.json" \
    || printf "    ${YLW}warn${OFF} could not write the deploy marker -- /health will report the previous commit\n"
}

# Printed when the code is back but the environment is not. Kept in one place
# because both rollback paths end the same way and the operator needs the same
# command from either.
venv_mismatch_warning() {
  local at=$1
  printf "\n${RED}VENV NOT ROLLED BACK${OFF}  the code is at %s but the pip install failed,\n" "$at"
  printf "so .venv still holds the versions the last deploy put there. Whatever the\n"
  printf "health check reported, production is running a code/dependency pair that has\n"
  printf "never been tested together. Once the dependency problem is fixed:\n"
  printf "    %s -m pip install -r %s/requirements.txt -c %s/constraints.txt\n" \
    "$PROD_PY" "$PROD" "$PROD"
  printf "    chown -R cas:cas %s/.venv\n" "$PROD"
  printf "    systemctl stop cas; sleep 3; systemctl start cas; systemctl restart cas-api\n"
  printf "If pip cannot be made to resolve, rebuild: rm -rf %s/.venv && python3 -m venv %s/.venv\n" \
    "$PROD" "$PROD"
}

# $STATE is a back-stack: entry 1 is where production stood before the most
# recent deploy. A rollback moves production ONTO one of those entries, so that
# entry stops being a way back and has to come off. Leaving it on is what made
# the automatic rollback lie: after a failed deploy the top of the stack named
# the commit production had just been reset to, so `--rollback 1` reset to where
# it already was and did nothing, and a real step back needed `--rollback 2` --
# a number nobody works out correctly while the site is down.
#
# The whole remainder is read into a variable before anything is written.
# `tail -n +2 "$STATE" > "$STATE"` truncates the file through the redirection
# before tail ever opens it; that exact shape once left this stack holding a
# single entry, and the fix there was the same one used here.
_stack_pop() {
  local n=${1:-1} rest
  [ -f "$STATE" ] || return 0
  rest=$(tail -n +"$((n + 1))" "$STATE")
  if [ -n "$rest" ]; then printf '%s\n' "$rest" > "$STATE.tmp"; else : > "$STATE.tmp"; fi
  mv "$STATE.tmp" "$STATE" || return 1
  log "stack: dropped $n from the top, $(wc -l < "$STATE") entries left"
}

[ "$(id -u)" -eq 0 ] || die "must run as root (systemctl, chown)"

# ── rollback ────────────────────────────────────────────────────────────────
if [ "$SHOW_HISTORY" -eq 1 ]; then
  [ -f "$STATE" ] || die "no deploys recorded in $STATE"
  # A stack popped empty by rollbacks is a file with no lines, not a missing
  # file. Without this the header prints over nothing at all.
  [ -s "$STATE" ] || die "$STATE is empty -- every recorded deploy point has been
       rolled back past. There is nothing further to roll back to."
  step "Recorded deploy points (newest first)"
  n=0
  while read -r c; do
    n=$((n+1))
    printf "  %2d  %s  %s\n" "$n" "$(git -C "$PROD" rev-parse --short "$c" 2>/dev/null || echo "$c")" \
      "$(git -C "$PROD" log -1 --format=%s "$c" 2>/dev/null || echo '(unknown commit)')"
  done < "$STATE"
  exit 0
fi

if [ "$ROLLBACK" -eq 1 ]; then
  # State is a stack, newest first. A single entry only ever let us undo the
  # most recent deploy; two bad deploys in a row left no recorded way back.
  [ -f "$STATE" ] || die "no previous deploy recorded in $STATE"
  PREV=$(sed -n "${ROLLBACK_N}p" "$STATE")
  [ -n "$PREV" ] || die "no deploy point $ROLLBACK_N back -- see: deploy.sh --history"
  step "Rolling back to $PREV"
  cd "$PROD" || die "cannot cd $PROD"
  git reset --hard "$PREV" || die "git reset failed"
  write_deploy_marker "$PREV" "rollback"
  chown -R cas:cas "$PROD"
  # Entries 1..N are now at or ahead of where production stands, and entry N is
  # exactly where it stands. Drop all N: the same reasoning as the automatic
  # path below, one entry there and N here. Verified against the entry actually
  # used rather than assumed, so an unexpected stack is left alone instead of
  # being truncated on a guess.
  if [ "$(sed -n "${ROLLBACK_N}p" "$STATE")" = "$PREV" ]; then
    _stack_pop "$ROLLBACK_N" \
      && ok "stack: $ROLLBACK_N entry(ies) dropped, top is now $(git rev-parse --short "$(sed -n 1p "$STATE")" 2>/dev/null || echo '(empty)')"
  else
    printf "    ${YLW}warn${OFF} stack entry %s is not %s -- left untouched\n" \
      "$ROLLBACK_N" "$(git rev-parse --short "$PREV")"
  fi
  # A failed pip does not stop the rollback -- the opposite of what gate 11
  # does with the same command, on purpose. Forward, stopping leaves production
  # untouched and serving, which is a safe place to stand. Here, stopping
  # leaves it stopped in the state being escaped: services still running the
  # code that was just reset away, or not running at all. Restoring service
  # outranks restoring the environment, so pip's failure is carried to the end
  # and shouted there rather than obeyed here.
  VENV_OK=1; sync_prod_venv || VENV_OK=0
  restart_services
  if health_check; then
    if [ "$VENV_OK" -eq 0 ]; then
      venv_mismatch_warning "$(git rev-parse --short "$PREV")"
      log "ROLLBACK -> $PREV: health ok, VENV SYNC FAILED"
      # Non-zero: the rollback landed but the machine is not in a state anyone
      # signed off on, and an exit 0 here would say it was.
      exit 1
    fi
    ok "rollback complete at $PREV"; log "ROLLBACK ok -> $PREV"; exit 0
  fi
  [ "$VENV_OK" -eq 0 ] && venv_mismatch_warning "$(git rev-parse --short "$PREV")"
  die "ROLLBACK FAILED HEALTH CHECK -- manual intervention required"
fi

# ── gates ───────────────────────────────────────────────────────────────────
step "1/13  Interpreters"
# Checked first, before the ~2m20s suite, because every later gate depends on
# it and the failure is a one-line fix.
#
# The ExecStart check is the point of this gate. A tree where some processes
# run from the venv and others from the system python is worse than either
# consistent state: the file that says which versions production runs would be
# true of only part of it, and the half that disagrees is the half nobody
# tested. So the deploy refuses to ship rather than maintain that state
# quietly. If this fires, production has not been migrated yet -- see the venv
# migration steps; do those first, then deploy.
[ -x "$STAGING_PY" ] || die "no staging interpreter at $STAGING_PY --
       build it with: python3 -m venv $STAGING/.venv"
[ -x "$PROD_PY" ] || die "no production interpreter at $PROD_PY --
       production has not been migrated to a venv yet; deploying now would
       ship a constraints.txt that nothing enforces."
# `systemctl show -p ExecStart --value`, not `systemctl cat`. cat prints the
# unit FILE (plus any drop-in, as separate text); show prints the value systemd
# will actually execute, with drop-ins already merged. A drop-in under
# /etc/systemd/system/cas.service.d/*.conf can replace ExecStart outright -- the
# documented way to override it -- and the old check would have kept passing
# while production ran from the system python again, which is the one state this
# gate exists to refuse. No drop-in exists for either unit today (checked
# 2026-08-27); the point is that adding one must not silently disarm the gate.
#
# The value looks like:
#   { path=/opt/cas/.venv/bin/python ; argv[]=/opt/cas/.venv/bin/python ... }
# so the match is on `path=` with a trailing space, which also rejects a longer
# path that merely starts the same way (.../bin/python3.13, say).
for _u in cas cas-api; do
  _exec=$(systemctl show "$_u" -p ExecStart --value 2>/dev/null)
  [ -n "$_exec" ] || die "systemctl reports no ExecStart for $_u --
       the unit is missing or unreadable, so this gate cannot tell which
       interpreter production would start. Deploy stops rather than assume."
  case "$_exec" in
    *"path=$PROD/.venv/bin/python "*) ;;
    *) die "$_u.service does not start from $PROD/.venv --
       production would run the system python while this deploy pins versions
       into a venv nothing uses. Effective ExecStart:
         $_exec
       Fix the unit (or the drop-in overriding it), daemon-reload, then deploy." ;;
  esac
done
ok "staging $($STAGING_PY -V 2>&1), production $($PROD_PY -V 2>&1), both units on venv"

step "2/13  Production working tree"
cd "$PROD" || die "cannot cd $PROD"
DIRTY=$(git status --porcelain)
if [ -n "$DIRTY" ]; then
  echo "$DIRTY"
  die "production has uncommitted changes -- someone edited it directly.
       Move the work to $STAGING, commit and push it, then deploy."
fi
ok "clean at $(git rev-parse --short HEAD)"

step "3/13  Fetching origin/main"
git fetch origin main -q || die "git fetch failed"
CURRENT=$(git rev-parse HEAD)
TARGET=$(git rev-parse origin/main)
ok "current $(git rev-parse --short HEAD)  target $(git rev-parse --short origin/main)"

if [ "$CURRENT" = "$TARGET" ]; then
  step "Already at origin/main -- nothing to deploy"; exit 0
fi

step "4/13  Incoming changes"
git --no-pager log --oneline "$CURRENT".."$TARGET"
echo
git --no-pager diff --stat "$CURRENT".."$TARGET"

step "5/13  Staging must be on the target commit, with a clean tree"
STAGING_HEAD=$(git -C "$STAGING" rev-parse HEAD 2>/dev/null) || die "cannot read $STAGING"
if [ "$STAGING_HEAD" != "$TARGET" ]; then
  die "staging is at $(git -C "$STAGING" rev-parse --short HEAD), target is $(git rev-parse --short origin/main).
       Deploy only what has actually run in staging:
         cd $STAGING && git fetch origin main && git reset --hard origin/main"
fi
# The HEAD check alone was not enough. Gate 8 runs the suite against the
# staging *working tree* while gate 12 ships the *commit* -- so uncommitted work
# in staging means the tests pass on code that is not what production receives,
# and, worse, code that IS in production goes out having never been tested. The
# same porcelain check gate 1 makes of production, for the same reason.
STAGING_DIRTY=$(git -C "$STAGING" status --porcelain)
if [ -n "$STAGING_DIRTY" ]; then
  echo "$STAGING_DIRTY"
  die "staging has uncommitted changes -- the suite would test this tree while
       the deploy ships $(git rev-parse --short "$TARGET"), which is not the same code.
       Commit and push the work in $STAGING, or discard it, then deploy."
fi
ok "staging is on $(git rev-parse --short origin/main), tree clean"

step "6/13  Restarting staging on the target commit"
# Placed here, before the suite and before production is touched, for two
# reasons.
#
# First, a long-running staging service serves whatever code it started with.
# cas-staging had been up since 2026-08-17 10:36 and was answering with
# day-old code: the same request returned 503 there and 403 in production, and
# the difference was diagnosed as a code bug twice before the restart showed it
# was not. The tests below and the smoke checks talk to these services, so a
# stale process makes the whole gate report on something other than the commit
# being deployed.
#
# Second, gate 5 has just established that staging *is* the target commit.
# That makes this the one moment where starting the services proves the commit
# boots, and it costs production nothing: launch_screen.py added an
# os.path.join to a module that never imported os -- valid syntax, NameError at
# import, and uvicorn imports the service graph at startup, so cas-api was down
# for six minutes on 2026-08-16. A restart here catches that class of failure
# in staging instead of in production.
#
# Staging failing to come up is therefore a hard stop: there is nothing worth
# deploying from a tree whose services do not start.
if ! restart_staging; then
  die "staging did not come up on $(git rev-parse --short "$TARGET") --
       production has not been touched. Check: journalctl -u cas-staging -u cas-api-staging -n 50"
fi
ok "staging is running the target commit"

step "7/13  Test database must be the migration chain's own schema"
# Gate 8 runs the suite against $TEST_DB, and that result is the last thing
# standing between a commit and production. It means something only if the
# schema those tests exercise is the schema the migration chain produces.
#
# It had drifted, and the drift went unnoticed for months. casdb_test's schema
# came from a hand-loaded pg_dump and never had an alembic_version table at
# all, so migration 0002 was never applied to it: the password_resets table
# production has carried since 2026-08-17 did not exist in the database this
# gate tests against. Nothing caught it, because no test touches that table --
# the three password-reset paths in cas_engine.py are untested. So the missing
# table cost nothing until the day someone tests them, at which point CI (which
# builds its database with `alembic upgrade head`) goes green and the deploy
# gate goes red, for a reason nobody would think to look for here.
#
# Rebuilt from the chain on 2026-08-26 and verified identical to production on
# all four dimensions: 27 tables, 360 columns, 88 indexes, 50 constraints, one
# view, zero differences. The rebuild fixes today; this gate is what keeps it
# true, because the next hand-applied DDL is exactly how it drifted the first
# time.
#
# Read through psql as the postgres role rather than psycopg2 with a DSN, so no
# credential is constructed, printed, or placed on a command line here.
_alembic_version_of() {
  # Empty output means "no alembic_version table" -- a real answer, and the one
  # this gate exists for. A psql failure is NOT an answer, and must never read
  # as a match: callers check the connection separately, below.
  sudo -u postgres psql -X -A -t -q -d "$1" \
       -c "SELECT version_num FROM alembic_version" 2>/dev/null
}
_dbname_of() { printf %s "${1%%\?*}" | sed 's#.*/##'; }

PROD_DB_NAME=$(_dbname_of "$(sed -n 's/^DB_URL=//p' "$PROD/.env" | tr -d "\"'")")
TEST_DB_NAME=$(_dbname_of "$TEST_DB")
[ -n "$PROD_DB_NAME" ] && [ -n "$TEST_DB_NAME" ] \
  || die "cannot read the production and test database names out of
       $PROD/.env and $STAGING/.env -- refusing to guess which databases to compare."

# Prove both databases answer before believing anything about their contents.
# A failed query returning empty would otherwise look exactly like a database
# with no alembic_version table, and two failed queries would look like a match.
for _db in "$PROD_DB_NAME" "$TEST_DB_NAME"; do
  sudo -u postgres psql -X -A -t -q -d "$_db" -c "SELECT 1" >/dev/null 2>&1 \
    || die "cannot query database '$_db' as the postgres role.
       This gate cannot tell whether the two schemas agree, so the deploy stops
       instead of assuming they do."
done

PROD_REV=$(_alembic_version_of "$PROD_DB_NAME")
TEST_REV=$(_alembic_version_of "$TEST_DB_NAME")

if [ -z "$TEST_REV" ]; then
  die "$TEST_DB_NAME has no alembic_version table, so its schema did not come from
       the migration chain, and gate 8 would test something other than what
       production runs. Production ($PROD_DB_NAME) is at: ${PROD_REV:-<none either>}

       Rebuild it from the migrations. Dump it first -- it is small and quick:
         sudo -u postgres pg_dump --clean --if-exists $TEST_DB_NAME \\
           | gzip -9 > /root/${TEST_DB_NAME}_pre_rebuild.sql.gz
         sudo -u postgres psql -c \"DROP DATABASE $TEST_DB_NAME\"
         sudo -u postgres psql -c \"CREATE DATABASE $TEST_DB_NAME OWNER cas\"
         cd $STAGING && DB_URL=\$(sed -n 's/^DB_URL=//p' .env | tr -d \"\\\"'\" \\
           | sed 's#/casdb_staging#/$TEST_DB_NAME#') .venv/bin/python -m alembic upgrade head

       Dropping the data is safe: the fixtures write their own rows and
       tests/integration/conftest.py seeds the admin the integrity tests need."
fi

if [ -z "$PROD_REV" ]; then
  die "$PROD_DB_NAME has no alembic_version table -- production's schema is not
       tracked by the migration chain, which makes this comparison meaningless.
       Stamp it only after confirming which revision the schema really is at:
         cd $STAGING && CAS_HOME=$STAGING .venv/bin/python -m alembic heads"
fi

# More than one line back means multiple heads recorded in one database.
if [ "$(printf %s\\n "$PROD_REV" | wc -l)" -gt 1 ] || [ "$(printf %s\\n "$TEST_REV" | wc -l)" -gt 1 ]; then
  die "a database records more than one alembic head
       ($PROD_DB_NAME: $(echo $PROD_REV) / $TEST_DB_NAME: $(echo $TEST_REV)).
       Resolve the branch in $STAGING/migrations before deploying."
fi

if [ "$PROD_REV" != "$TEST_REV" ]; then
  die "schema revision mismatch -- gate 8 would test a schema production does not run.
         $TEST_DB_NAME is at: $TEST_REV
         $PROD_DB_NAME is at: $PROD_REV    <- the revision the tests must match

       Bring the test database up to production's revision:
         cd $STAGING && DB_URL=\$(sed -n 's/^DB_URL=//p' .env | tr -d \"\\\"'\" \\
           | sed 's#/casdb_staging#/$TEST_DB_NAME#') .venv/bin/python -m alembic upgrade head

       If it is production that is behind, do not fix it from here: schema
       changes on production are applied by hand, deliberately, and this script
       never writes DDL."
fi

# Deliberately a warning and not a stop. A migration added in the target commit
# leaves both databases agreeing at the older revision -- which is a correct
# state, because production's DDL is applied by hand and this script does not
# apply it. But the suite about to run does not cover that migration, so say it
# out loud rather than let a matching pair imply coverage it does not have.
CHAIN_HEAD=$(cd "$STAGING" && CAS_HOME="$STAGING" "$STAGING_PY" -m alembic heads 2>/dev/null \
             | sed -n 's/ (head)$//p' | head -1)
if [ -n "$CHAIN_HEAD" ] && [ "$CHAIN_HEAD" != "$PROD_REV" ]; then
  printf "    ${YLW}warn${OFF} both databases are at %s; the chain head is %s\n" "$PROD_REV" "$CHAIN_HEAD"
  printf "         gate 8 will not exercise the pending migration. Apply it to\n"
  printf "         production by hand after this deploy, then rebuild %s.\n" "$TEST_DB_NAME"
fi
ok "$TEST_DB_NAME and $PROD_DB_NAME both at $PROD_REV"

step "8/13  Test suite (in staging)"
# Mask the DSN before printing: the derived value carries the database
# password, and this line lands in terminal scrollback on every deploy.
echo "    running in $STAGING against $(printf %s "$TEST_DB" | sed -E 's#://[^:]+:[^@]+@#://***:***@#') -- production is not touched"
echo "    smoke endpoints: staging engine :8775 (the target commit, restarted in gate 6)"
TESTLOG=$(mktemp)
# $STAGING_PY, not python3. The services under test start from
# $STAGING/.venv/bin/python, so the system python3 would have tested a set of
# versions no instance runs: measured on 2026-08-24, the two interpreters
# resolved 6 of the 58 constrained packages differently -- starlette 1.2.1 vs
# 1.3.1 and PyJWT 2.7.0 vs 2.13.0 among them, i.e. the request layer and the
# token layer. A green suite from the wrong interpreter is the failure this
# whole change exists to remove, and it is silent.
# SMOKE_BASE_URL points the smoke suite at STAGING's engine, not production's.
#
# It defaulted to 127.0.0.1:8765 -- production -- so half of this gate judged the
# commit (unit and integration tests, against the staging tree) and the other
# half judged the machine the commit is about to replace. That is the same shape
# as the finding a week ago that pytest was reporting on production's files: a
# gate whose answer is about the wrong tree.
#
# Gate 6 has already restarted staging on $TARGET and waited for it to answer,
# so :8775 is the target commit, running. That is what a gate exists to judge.
# Production is still on the old commit; measuring it here would mean a good
# commit could be rejected for a fault it fixes, and a bad one accepted because
# the old code was healthy.
#
# SMOKE_TARGET=staging is set explicitly rather than left to be inferred from
# the port, so the intent survives someone changing the port. It makes the suite
# skip its deployment-health checks -- "did the CDM cron run", "is the catalog
# cache fresh" -- which are meaningless here: staging has no cron by design, so
# its data is frozen and those questions say nothing about the commit. They keep
# running against production every night through scripts/run_smoke_cron.sh,
# which is where they mean something.
# CAS_TEST_LOCK_WAIT: this gate waits for the test database instead of failing
# on it. tests/integration/conftest.py takes an exclusive lock on $TEST_DB for
# the length of a run, because two concurrent runs write to one database and
# corrupt each other's results silently -- which for this gate would mean going
# green on somebody else's side effects.
#
# A manual run refuses immediately (the operator is at the keyboard and can
# decide); this one waits, because gates 1-7 have already run and aborting
# throws that work away, while the manual run it collided with is usually
# seconds from finishing. 180s is generous next to the ~2m suite it is waiting
# for, and the wait announces itself on the terminal as it happens, so a hung
# deploy is never a mystery. If it expires the gate stops with the same message
# and names the process holding the lock.
( cd "$STAGING" && TEST_DB_URL="$TEST_DB" \
    SMOKE_BASE_URL="http://127.0.0.1:8775" SMOKE_TARGET="staging" \
    CAS_TEST_LOCK_WAIT=180 \
    timeout 600 "$STAGING_PY" -m pytest -q ) >"$TESTLOG" 2>&1
TESTRC=$?
tail -5 "$TESTLOG"
if [ "$TESTRC" -ne 0 ]; then
  echo "    full output: $TESTLOG"
  die "tests failed (pytest exit $TESTRC) -- not deploying"
fi
rm -f "$TESTLOG"
ok "suite passed"

if [ "$AUTO_YES" -eq 0 ]; then
  step "9/13  Confirm"
  read -r -p "    Deploy $(git rev-parse --short "$TARGET") to production? [y/N] " ans
  case "$ans" in y|Y|yes) ;; *) die "cancelled by operator" ;; esac
else
  step "9/13  Confirm -- skipped (--yes)"
fi

step "10/13  Backing up the database"
# The rollback point is NOT recorded here. It used to be, and that opened a
# window onto the same defect O-3 is about: this step runs before gate 11 and
# gate 12, so between them the stack names the commit production is still
# running. Close the window the wrong way -- gate 11's pip failure is a
# deliberate die, and an interrupted run ends the same way -- and the entry
# stays, naming a commit production never left, with `--rollback 1` then a
# no-op. The window was measured rather than argued: sampling $STATE during the
# 082f571 deploy on 2026-08-27 showed a17d531 on top while production was still
# serving a17d531, for the ~3 minutes gate 11 took. That deploy went on to
# succeed, so the entry became true -- but only because nothing failed in
# between. The push now happens in gate 12, in the same breath as the reset
# that makes it true.
if [ -x "$PROD/scripts/backup_db.sh" ]; then
  "$PROD/scripts/backup_db.sh" >/dev/null 2>&1 && ok "database backed up" \
    || printf "    ${YLW}warn${OFF} backup script returned non-zero\n"
else
  printf "    ${YLW}warn${OFF} no backup_db.sh -- deploying without a fresh dump\n"
fi

step "11/13  Syncing the production venv"
# Bringing the environment to the target state is part of the deploy, not a
# chore to remember afterwards. requirements.txt and constraints.txt described
# an environment nothing enforced; this is the line that enforces it.
#
# Unconditional, on every deploy. The alternative was to detect a dependency
# change with `git diff --name-only "$CURRENT".."$TARGET"` and skip the
# install otherwise, and it was rejected twice over. The saving is not real: a
# no-op install against an already-correct venv measured 3.4s on 2026-08-24,
# next to the ~2m20s this deploy already spends in the suite. And the check
# would miss the cases that matter most -- a venv left half-built by an
# interrupted install, or one moved by a stray `pip install`, produces no diff
# at all, so precisely the drift worth catching is the drift a diff-gated
# install skips. Running always makes the environment a function of the files
# in the tree rather than of the deploy history.
#
# Read from staging's copies, not production's. Gate 5 established that
# staging is on $TARGET with a clean tree, so these two files are byte for
# byte the ones production is about to receive -- and taking them from there
# means the venv reaches the target state while production's code is still
# untouched, so everything below can still fail safe.
[ -f "$STAGING/requirements.txt" ] && [ -f "$STAGING/constraints.txt" ] \
  || die "requirements.txt or constraints.txt missing from $STAGING"
"$PROD_PY" -m pip install -q \
  -r "$STAGING/requirements.txt" -c "$STAGING/constraints.txt"
PIPRC=$?
# Unconditionally, before the exit check: pip runs as root here and writes
# root-owned files into a tree the services read as cas. Gate 12 chowns $PROD
# on the way past, but the die below never reaches it, and the resulting
# failure looks like a missing module rather than a permission problem.
chown -R cas:cas "$PROD/.venv"
if [ "$PIPRC" -ne 0 ]; then
  # Fatal, deliberately. Of the three states a deploy can end in -- shipped,
  # not shipped, or shipped onto a broken interpreter -- only the third
  # survives a restart and outlives the terminal it happened in. Stopping here
  # leaves production serving the commit it was already serving.
  die "pip install into $PROD/.venv failed (exit $PIPRC) -- production still at
       $(git rev-parse --short HEAD) and running. The venv may be partially
       written: re-run this deploy once the dependency problem is fixed, or
       rebuild it with
         rm -rf $PROD/.venv && python3 -m venv $PROD/.venv &&
         $PROD_PY -m pip install -r $PROD/requirements.txt -c $PROD/constraints.txt"
fi
# pip resolving cleanly is not the same as the packages importing. A wheel can
# install and still fail at import -- and uvicorn imports the whole service
# graph at startup, so an import error here becomes a dead cas-api rather than
# a failed deploy. Cheaper to find it now, with production still up.
if ! "$PROD_PY" -c "import fastapi, uvicorn, pydantic, pydantic_settings, jwt, bcrypt, psycopg2, numpy, scipy, sgp4, pandas, xgboost, shap, openpyxl, reportlab, httpx, pymsis" 2>&1; then
  die "the production venv installed but does not import -- production still at
       $(git rev-parse --short HEAD) and running. Fix before deploying."
fi
ok "production venv in sync and importing"

step "12/13  Updating production and recording the rollback point"
git reset --hard "$TARGET" || die "git reset failed"
write_deploy_marker "$TARGET" "origin/main"
chown -R cas:cas "$PROD"
# Recorded here and nowhere earlier: production has just left $CURRENT, which
# is the fact this entry asserts. Anything that fails before this line leaves
# production where it was and the stack untouched, and anything that fails
# after it -- gate 13's health check included -- has a real way back on top.
#
# Prepend, keeping 20: the stack is what makes --rollback N possible.
# Read the old contents into a variable first. Piping `cat - "$STATE"` into a
# temp file looked safe but only ever recorded one entry -- the shell had
# already truncated the target through the redirection before cat read it.
_prev_state=""
[ -f "$STATE" ] && _prev_state=$(cat "$STATE")
{ printf '%s\n' "$CURRENT"; [ -n "$_prev_state" ] && printf '%s\n' "$_prev_state"; } \
  | head -20 > "$STATE.tmp"
mv "$STATE.tmp" "$STATE"
ok "at $(git rev-parse --short HEAD), rollback point $(git rev-parse --short "$CURRENT") -> $STATE"
log "DEPLOY $(git rev-parse --short "$CURRENT") -> $(git rev-parse --short "$TARGET")"

step "13/13  Restarting production and checking health"
restart_services
if health_check; then
  printf "\n${GRN}DEPLOY OK${OFF}  %s -> %s\n" \
    "$(git rev-parse --short "$CURRENT")" "$(git rev-parse --short "$TARGET")"
  log "DEPLOY ok -> $(git rev-parse --short "$TARGET")"
  exit 0
fi

printf "\n${RED}HEALTH CHECK FAILED -- rolling back${OFF}\n"
log "HEALTH FAIL -> rolling back to $(git rev-parse --short "$CURRENT")"
git reset --hard "$CURRENT" || die "ROLLBACK GIT RESET FAILED -- manual intervention required"
write_deploy_marker "$CURRENT" "rollback-after-failed-deploy"
chown -R cas:cas "$PROD"
# Gate 12 pushed $CURRENT a minute ago as the way back from this deploy. The
# deploy failed and production is on $CURRENT again, so that entry is no longer
# a way back -- it is where we are. Popped here, immediately after the
# reset rather than after the health check, so the stack is right for every
# branch below including the ones that end in manual intervention: that is
# precisely when someone types --rollback and needs the number to mean what it
# says.
if [ "$(sed -n 1p "$STATE" 2>/dev/null)" = "$CURRENT" ]; then
  _stack_pop 1 \
    && ok "stack: $(git rev-parse --short "$CURRENT") dropped, top is now $(git rev-parse --short "$(sed -n 1p "$STATE")" 2>/dev/null || echo '(empty)')"
else
  printf "    ${YLW}warn${OFF} stack top is not %s -- left untouched\n" "$(git rev-parse --short "$CURRENT")"
fi
# Gate 11 moved the venv to $TARGET's dependency set before the code moved.
# Undoing the code without undoing that leaves $CURRENT running against
# $TARGET's packages -- and this branch is reached precisely because something
# about the new state failed health, so leaving half of it in place is the
# worst of the three outcomes. Same as --rollback: pip failing here is reported
# at the end, not obeyed. See sync_prod_venv().
VENV_OK=1; sync_prod_venv || VENV_OK=0
restart_services
if health_check; then
  if [ "$VENV_OK" -eq 0 ]; then
    venv_mismatch_warning "$(git rev-parse --short "$CURRENT")"
    log "ROLLBACK after failed deploy: health ok, VENV SYNC FAILED"
    exit 1
  fi
  printf "\n${YLW}Rolled back to %s. The deploy was rejected, production is up.${OFF}\n" \
    "$(git rev-parse --short "$CURRENT")"
  log "ROLLBACK ok after failed deploy"
  exit 1
fi
[ "$VENV_OK" -eq 0 ] && venv_mismatch_warning "$(git rev-parse --short "$CURRENT")"
die "ROLLBACK ALSO FAILED HEALTH CHECK -- production may be down, intervene now"
