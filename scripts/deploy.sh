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
#   deploy.sh            interactive, asks before touching production
#   deploy.sh --yes      no prompt (for CI later; still runs every gate)
#   deploy.sh --rollback return to the commit recorded by the last deploy
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

AUTO_YES=0; ROLLBACK=0
for a in "$@"; do
  case "$a" in
    --yes|-y)    AUTO_YES=1 ;;
    --rollback)  ROLLBACK=1 ;;
    *) echo "unknown argument: $a"; exit 2 ;;
  esac
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

restart_services() {
  # stop -> sleep -> start, not restart: the engine binds 8765 and a plain
  # restart has raced its own shutdown before. cas-api needs ~20s because each
  # uvicorn worker loads XGBoost and a SHAP explainer at startup.
  systemctl stop cas; sleep 3; systemctl start cas; sleep 10
  systemctl restart cas-api; sleep 25
}

[ "$(id -u)" -eq 0 ] || die "must run as root (systemctl, chown)"

# ── rollback ────────────────────────────────────────────────────────────────
if [ "$ROLLBACK" -eq 1 ]; then
  [ -f "$STATE" ] || die "no previous deploy recorded in $STATE"
  PREV=$(cat "$STATE")
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
step "1/9  Production working tree"
cd "$PROD" || die "cannot cd $PROD"
DIRTY=$(git status --porcelain)
if [ -n "$DIRTY" ]; then
  echo "$DIRTY"
  die "production has uncommitted changes -- someone edited it directly.
       Move the work to $STAGING, commit and push it, then deploy."
fi
ok "clean at $(git rev-parse --short HEAD)"

step "2/9  Fetching origin/main"
git fetch origin main -q || die "git fetch failed"
CURRENT=$(git rev-parse HEAD)
TARGET=$(git rev-parse origin/main)
ok "current $(git rev-parse --short HEAD)  target $(git rev-parse --short origin/main)"

if [ "$CURRENT" = "$TARGET" ]; then
  step "Already at origin/main -- nothing to deploy"; exit 0
fi

step "3/9  Incoming changes"
git --no-pager log --oneline "$CURRENT".."$TARGET"
echo
git --no-pager diff --stat "$CURRENT".."$TARGET"

step "4/9  Staging must be on the target commit"
STAGING_HEAD=$(git -C "$STAGING" rev-parse HEAD 2>/dev/null) || die "cannot read $STAGING"
if [ "$STAGING_HEAD" != "$TARGET" ]; then
  die "staging is at $(git -C "$STAGING" rev-parse --short HEAD), target is $(git rev-parse --short origin/main).
       Deploy only what has actually run in staging:
         cd $STAGING && git fetch origin main && git reset --hard origin/main"
fi
ok "staging is on $(git rev-parse --short origin/main)"

step "5/9  Test suite (in staging)"
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
  step "6/9  Confirm"
  read -r -p "    Deploy $(git rev-parse --short "$TARGET") to production? [y/N] " ans
  case "$ans" in y|Y|yes) ;; *) die "cancelled by operator" ;; esac
else
  step "6/9  Confirm -- skipped (--yes)"
fi

step "7/9  Recording rollback point and backing up the database"
echo "$CURRENT" > "$STATE"
ok "rollback point $(git rev-parse --short "$CURRENT") -> $STATE"
if [ -x "$PROD/scripts/backup_db.sh" ]; then
  "$PROD/scripts/backup_db.sh" >/dev/null 2>&1 && ok "database backed up" \
    || printf "    ${YLW}warn${OFF} backup script returned non-zero\n"
else
  printf "    ${YLW}warn${OFF} no backup_db.sh -- deploying without a fresh dump\n"
fi

step "8/9  Updating production"
git reset --hard "$TARGET" || die "git reset failed"
chown -R cas:cas "$PROD"
ok "at $(git rev-parse --short HEAD)"
log "DEPLOY $(git rev-parse --short "$CURRENT") -> $(git rev-parse --short "$TARGET")"

step "9/9  Restarting and checking health"
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
