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

[ "$(id -u)" -eq 0 ] || die "must run as root (systemctl, chown)"

# ── rollback ────────────────────────────────────────────────────────────────
if [ "$SHOW_HISTORY" -eq 1 ]; then
  [ -f "$STATE" ] || die "no deploys recorded in $STATE"
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
  chown -R cas:cas "$PROD"
  restart_services
  if health_check; then
    ok "rollback complete at $PREV"; log "ROLLBACK ok -> $PREV"; exit 0
  fi
  die "ROLLBACK FAILED HEALTH CHECK -- manual intervention required"
fi

# ── gates ───────────────────────────────────────────────────────────────────
step "1/10  Production working tree"
cd "$PROD" || die "cannot cd $PROD"
DIRTY=$(git status --porcelain)
if [ -n "$DIRTY" ]; then
  echo "$DIRTY"
  die "production has uncommitted changes -- someone edited it directly.
       Move the work to $STAGING, commit and push it, then deploy."
fi
ok "clean at $(git rev-parse --short HEAD)"

step "2/10  Fetching origin/main"
git fetch origin main -q || die "git fetch failed"
CURRENT=$(git rev-parse HEAD)
TARGET=$(git rev-parse origin/main)
ok "current $(git rev-parse --short HEAD)  target $(git rev-parse --short origin/main)"

if [ "$CURRENT" = "$TARGET" ]; then
  step "Already at origin/main -- nothing to deploy"; exit 0
fi

step "3/10  Incoming changes"
git --no-pager log --oneline "$CURRENT".."$TARGET"
echo
git --no-pager diff --stat "$CURRENT".."$TARGET"

step "4/10  Staging must be on the target commit, with a clean tree"
STAGING_HEAD=$(git -C "$STAGING" rev-parse HEAD 2>/dev/null) || die "cannot read $STAGING"
if [ "$STAGING_HEAD" != "$TARGET" ]; then
  die "staging is at $(git -C "$STAGING" rev-parse --short HEAD), target is $(git rev-parse --short origin/main).
       Deploy only what has actually run in staging:
         cd $STAGING && git fetch origin main && git reset --hard origin/main"
fi
# The HEAD check alone was not enough. Gate 6 runs the suite against the
# staging *working tree* while gate 9 ships the *commit* -- so uncommitted work
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

step "5/10  Restarting staging on the target commit"
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
# Second, gate 4 has just established that staging *is* the target commit.
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

step "6/10  Test suite (in staging)"
# Mask the DSN before printing: the derived value carries the database
# password, and this line lands in terminal scrollback on every deploy.
echo "    running in $STAGING against $(printf %s "$TEST_DB" | sed -E 's#://[^:]+:[^@]+@#://***:***@#') -- production is not touched"
TESTLOG=$(mktemp)
( cd "$STAGING" && TEST_DB_URL="$TEST_DB" timeout 600 python3 -m pytest -q ) >"$TESTLOG" 2>&1
TESTRC=$?
tail -5 "$TESTLOG"
if [ "$TESTRC" -ne 0 ]; then
  echo "    full output: $TESTLOG"
  die "tests failed (pytest exit $TESTRC) -- not deploying"
fi
rm -f "$TESTLOG"
ok "suite passed"

if [ "$AUTO_YES" -eq 0 ]; then
  step "7/10  Confirm"
  read -r -p "    Deploy $(git rev-parse --short "$TARGET") to production? [y/N] " ans
  case "$ans" in y|Y|yes) ;; *) die "cancelled by operator" ;; esac
else
  step "7/10  Confirm -- skipped (--yes)"
fi

step "8/10  Recording rollback point and backing up the database"
# Prepend, keeping 20: the stack is what makes --rollback N possible.
# Read the old contents into a variable first. Piping `cat - "$STATE"` into a
# temp file looked safe but only ever recorded one entry -- the shell had
# already truncated the target through the redirection before cat read it.
_prev_state=""
[ -f "$STATE" ] && _prev_state=$(cat "$STATE")
{ printf '%s\n' "$CURRENT"; [ -n "$_prev_state" ] && printf '%s\n' "$_prev_state"; } \
  | head -20 > "$STATE.tmp"
mv "$STATE.tmp" "$STATE"
ok "rollback point $(git rev-parse --short "$CURRENT") -> $STATE"
if [ -x "$PROD/scripts/backup_db.sh" ]; then
  "$PROD/scripts/backup_db.sh" >/dev/null 2>&1 && ok "database backed up" \
    || printf "    ${YLW}warn${OFF} backup script returned non-zero\n"
else
  printf "    ${YLW}warn${OFF} no backup_db.sh -- deploying without a fresh dump\n"
fi

step "9/10  Updating production"
git reset --hard "$TARGET" || die "git reset failed"
chown -R cas:cas "$PROD"
ok "at $(git rev-parse --short HEAD)"
log "DEPLOY $(git rev-parse --short "$CURRENT") -> $(git rev-parse --short "$TARGET")"

step "10/10  Restarting production and checking health"
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
chown -R cas:cas "$PROD"
restart_services
if health_check; then
  printf "\n${YLW}Rolled back to %s. The deploy was rejected, production is up.${OFF}\n" \
    "$(git rev-parse --short "$CURRENT")"
  log "ROLLBACK ok after failed deploy"
  exit 1
fi
die "ROLLBACK ALSO FAILED HEALTH CHECK -- production may be down, intervene now"
