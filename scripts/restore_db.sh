#!/bin/bash
# ═══════════════════════════════════════════════════════════════
# CAS Platform — PostgreSQL Restore Script
# ═══════════════════════════════════════════════════════════════
# Usage:
#   ./restore_db.sh <backup_file.sql.gz>
#   ./restore_db.sh --latest
#   ./restore_db.sh --list
#
#   CAS_RESTORE_DB=casdb_restoretest ./restore_db.sh --latest
#
# CAS_RESTORE_DB selects the target database; unset means casdb, i.e. live
# production. Any other target is a DRILL: the whole point of this script is
# that a backup you have never restored is not a backup you know you have, and
# with the name hard-coded the only way to exercise it was to restore
# production. A drill therefore skips the service stop/start -- taking the
# platform down to load casdb_restoretest proves nothing -- but still takes a
# safety dump of the target (it is being overwritten either way) and still runs
# the row-count verification, which is the part actually under test.
#
# This script used to report "RESTORE COMPLETED SUCCESSFULLY" after a restore
# that had done nothing. Four separate reasons, all fixed below:
#
#   1. psql ran without -v ON_ERROR_STOP=1, so it kept going after the first
#      failed statement and still exited 0.
#   2. The restore pipeline ended in `> /dev/null 2>&1`, so the errors psql
#      did print were thrown away and never reached the log or the operator.
#   3. Success was decided by `systemctl is-active cas`. The engine starts
#      happily against an empty database, so "ACTIVE" proved nothing about
#      the data.
#   4. cas-api was never stopped. It stayed connected to the same database
#      through the restore, holding sessions open against tables being
#      dropped and recreated underneath it.
#
# The success criterion is now the data: the restore must apply without a
# single error AND the tables that must never be empty must not be empty.
# The restore runs in one transaction, so a failure leaves the target exactly
# as it was rather than half-loaded.
# ═══════════════════════════════════════════════════════════════

set -euo pipefail

BACKUP_ROOT="/opt/cas/backups/db"

# Target database. Unset -> casdb, so nothing about the production path changes
# for an operator who runs this the way they always did.
PROD_DB="casdb"
DB_NAME="${CAS_RESTORE_DB:-$PROD_DB}"

# Only a restore of the live database has services in front of it to stop and
# start. Every other target is a drill against a scratch database that nothing
# is connected to.
if [ "$DB_NAME" = "$PROD_DB" ]; then
    IS_PROD_TARGET=1
    MODE_LABEL="PRODUCTION restore"
else
    IS_PROD_TARGET=0
    MODE_LABEL="DRILL (services untouched)"
fi

# Safety backups live under /root, not /tmp. /tmp is cleared on reboot (and is
# tmpfs on some of these hosts), so the one copy of the pre-restore database
# could disappear exactly when a failed restore made it the only good copy.
SAFETY_DIR="/root/cas_restore_safety"

# Every byte psql writes goes here. Nothing is sent to /dev/null.
LOG_DIR="/var/log/cas"
TS=$(date +%Y%m%d_%H%M%S)
RESTORE_LOG="$LOG_DIR/restore_${TS}.log"

# Tables that are never legitimately empty in a real CAS database. An empty
# one means the dump did not apply, whatever psql's exit code said.
VERIFY_TABLES="users watchlist conjunction_events"

usage() {
    echo "Usage:"
    echo "  $0 <backup_file.sql.gz>   # restore from specific file"
    echo "  $0 --latest               # restore from most recent daily backup"
    echo "  $0 --list                 # show available backups"
    echo ""
    echo "Environment:"
    echo "  CAS_RESTORE_DB=<dbname>   # target database (default: $PROD_DB)"
    echo "                            # anything other than $PROD_DB is a drill:"
    echo "                            # services are NOT stopped or started, but"
    echo "                            # the safety dump and the row-count"
    echo "                            # verification run exactly as they do live."
    echo ""
    echo "  Rehearse a restore without touching production:"
    echo "    CAS_RESTORE_DB=casdb_restoretest $0 --latest"
    exit 1
}

if [ $# -lt 1 ]; then
    usage
fi

# --- list option ---
if [ "$1" = "--list" ]; then
    echo "=== Available backups ==="
    echo ""
    echo "Daily:"
    ls -lah "$BACKUP_ROOT/daily/" | grep -E "\.sql\.gz$" || echo "  (none)"
    echo ""
    echo "Weekly:"
    ls -lah "$BACKUP_ROOT/weekly/" | grep -E "\.sql\.gz$" || echo "  (none)"
    echo ""
    echo "Monthly:"
    ls -lah "$BACKUP_ROOT/monthly/" | grep -E "\.sql\.gz$" || echo "  (none)"
    exit 0
fi

# --- latest option ---
if [ "$1" = "--latest" ]; then
    BACKUP_FILE=$(ls -t "$BACKUP_ROOT/daily"/*.sql.gz 2>/dev/null | head -1)
    if [ -z "$BACKUP_FILE" ]; then
        echo "ERROR: No backups found in $BACKUP_ROOT/daily/"
        exit 1
    fi
else
    BACKUP_FILE="$1"
fi

# --- validate file exists ---
if [ ! -f "$BACKUP_FILE" ]; then
    echo "ERROR: Backup file not found: $BACKUP_FILE"
    exit 1
fi

# --- validate gzip integrity ---
if ! gzip -t "$BACKUP_FILE" 2>/dev/null; then
    echo "ERROR: Backup file is corrupted (gzip test failed): $BACKUP_FILE"
    exit 1
fi

# --- root required (systemctl, sudo -u postgres, /root, /var/log/cas) ---
if [ "$(id -u)" -ne 0 ]; then
    echo "ERROR: must run as root (systemctl, sudo -u postgres, $SAFETY_DIR)"
    exit 1
fi

mkdir -p "$LOG_DIR"
mkdir -p "$SAFETY_DIR"
chmod 700 "$SAFETY_DIR"

# Wait for a service to actually answer instead of guessing how long it takes.
# Same shape as deploy.sh: `systemctl is-active` reports active as soon as the
# unit's process exists, while cas-api's uvicorn workers are still importing
# XGBoost and their SHAP explainers. Only the endpoint knows.
wait_for_health() {
    local label=$1 url=$2 timeout=${3:-90}
    local start elapsed
    start=$(date +%s)
    while :; do
        if curl -fsS --max-time 5 "$url" >/dev/null 2>&1; then
            elapsed=$(( $(date +%s) - start ))
            echo "      $label ready in ${elapsed}s"
            return 0
        fi
        elapsed=$(( $(date +%s) - start ))
        [ "$elapsed" -ge "$timeout" ] && break
        sleep 3
    done
    echo "      WARNING: $label did not answer within ${timeout}s"
    return 1
}

# Count rows in one table. Prints the count on stdout, returns non-zero if the
# query itself failed. -X ignores ~/.psqlrc, -A -t gives a bare number, and
# ON_ERROR_STOP=1 turns a missing table into a non-zero exit instead of an
# empty line -- a comparison that silently returned nothing twice before is
# exactly how a broken restore got reported as fine.
count_rows() {
    local tbl=$1
    sudo -u postgres psql -X -A -t -q -v ON_ERROR_STOP=1 \
        -d "$DB_NAME" -c "SELECT count(*) FROM ${tbl};" 2>>"$RESTORE_LOG"
}

start_services() {
    # Engine first, then the API that talks to it. Both were stopped for the
    # restore, so this is a plain start -- no stop/sleep/start dance needed.
    echo "      Starting cas.service..."
    systemctl start cas
    wait_for_health "engine  /health" "http://localhost:8765/health" 90 || true
    echo "      Starting cas-api.service..."
    systemctl start cas-api
    wait_for_health "api     /api/v2/health" "http://127.0.0.1:8766/api/v2/health" 90 || true
}

# Step numbering differs by mode: a drill has no services to stop or start.
if [ "$IS_PROD_TARGET" -eq 1 ]; then TOTAL_STEPS=6; else TOTAL_STEPS=3; fi
STEP_N=0
step() {
    STEP_N=$((STEP_N + 1))
    echo ""
    echo "[$STEP_N/$TOTAL_STEPS] $*"
}

# --- confirmation prompt ---
SIZE=$(du -h "$BACKUP_FILE" | cut -f1)
echo "═══════════════════════════════════════════════════════════"
echo "WARNING: This will REPLACE the target database!"
echo "═══════════════════════════════════════════════════════════"
echo "TARGET DB:   $DB_NAME"
echo "Mode:        $MODE_LABEL"
if [ "$IS_PROD_TARGET" -eq 1 ]; then
    echo "             cas and cas-api WILL be stopped and restarted."
else
    echo "             $PROD_DB is not touched. cas and cas-api keep running."
    echo "             Safety dump and row-count verification run as they do live."
fi
echo "Backup file: $BACKUP_FILE"
echo "Backup size: $SIZE"
echo "Backup date: $(stat -c %y "$BACKUP_FILE" | cut -d. -f1)"
echo "Restore log: $RESTORE_LOG"
echo "═══════════════════════════════════════════════════════════"
echo ""
read -p "Type 'YES' to restore into '$DB_NAME': " CONFIRM
if [ "$CONFIRM" != "YES" ]; then
    echo "Restore cancelled."
    exit 0
fi

# --- pre-restore safety backup ---
# Taken for every target, drill included: whatever is in $DB_NAME now is about
# to be dropped, and "it was only the test database" is a thing you find out is
# wrong after the fact. The filename carries the database name so a drill dump
# can never be mistaken for a production one.
SAFETY_BACKUP="$SAFETY_DIR/${DB_NAME}_safety_${TS}.sql.gz"
BACK_CMD="$0 $SAFETY_BACKUP"
[ "$IS_PROD_TARGET" -eq 1 ] || BACK_CMD="CAS_RESTORE_DB=$DB_NAME $BACK_CMD"

step "Creating safety backup of $DB_NAME at: $SAFETY_BACKUP"
if ! sudo -u postgres pg_dump "$DB_NAME" --no-owner --no-acl --clean --if-exists \
     2>>"$RESTORE_LOG" | gzip > "$SAFETY_BACKUP"; then
    echo "ERROR: safety backup of $DB_NAME failed -- refusing to restore without one."
    echo "       See $RESTORE_LOG"
    rm -f "$SAFETY_BACKUP"
    exit 1
fi
chmod 600 "$SAFETY_BACKUP"
echo "      Safety backup OK ($(du -h "$SAFETY_BACKUP" | cut -f1))"

# --- stop BOTH services (production target only) ---
# cas-api first: it is the one holding pooled connections to $PROD_DB, and
# leaving it up meant the restore ran against a database with live sessions
# still reading and writing the tables being dropped.
if [ "$IS_PROD_TARGET" -eq 1 ]; then
    step "Stopping cas-api.service and cas.service..."
    systemctl stop cas-api
    systemctl stop cas
    sleep 3
    echo "      Both services stopped"
else
    echo ""
    echo "      (drill: not stopping cas/cas-api -- they do not use $DB_NAME)"
fi

# --- restore ---
# ON_ERROR_STOP=1: without it psql runs every remaining statement after the
# first failure and still exits 0, which is how a restore that applied almost
# nothing reported success. Output goes to $RESTORE_LOG, never to /dev/null.
#
# --single-transaction: all of the dump or none of it. Together with
# ON_ERROR_STOP=1 this makes a failed restore leave $DB_NAME byte-for-byte as
# it was, instead of half-dropped and half-loaded. The alternative -- keeping
# whatever applied before the error -- is worse in every case that matters
# here: this script exists for the moment production is already down, and a
# partially restored database is not a system you can serve from, it is one
# more unknown state to diagnose while the clock runs. Rolling back costs
# nothing we do not already have, because the previous state is exactly what
# step 1 dumped anyway.
#
# Measured, not assumed, on 2026-08-18 against casdb_restoretest:
#   19:32  without --single-transaction, into an empty target: 135s, 0 errors
#   19:36  with --single-transaction, into a full target:      150s, 0 errors
#          -- so the DROP/CREATE this dump performs is transactional here, and
#          holding both copies for the length of the transaction fits
#          (2.1 GB database, 93 GB free)
#   19:42  a dump that drops users, inserts one row and then errors: psql
#          exits non-zero, this script exits 1, and users still holds its
#          original 7 rows. Nothing was applied. That is the case the old
#          script reported as "RESTORE COMPLETED SUCCESSFULLY".
step "Restoring $DB_NAME from $BACKUP_FILE..."
echo "      psql output -> $RESTORE_LOG"
RESTORE_START=$(date +%s)
if ! gunzip -c "$BACKUP_FILE" \
     | sudo -u postgres psql -X -q -v ON_ERROR_STOP=1 --single-transaction \
       -d "$DB_NAME" >>"$RESTORE_LOG" 2>&1; then
    RESTORE_SECS=$(( $(date +%s) - RESTORE_START ))
    echo ""
    echo "ERROR: Restore of $DB_NAME FAILED after ${RESTORE_SECS}s. Last lines of $RESTORE_LOG:"
    tail -20 "$RESTORE_LOG" | sed 's/^/      /'
    if [ "$IS_PROD_TARGET" -eq 1 ]; then
        echo ""
        echo "      Bringing services back up on the database as it now stands."
        start_services
    fi
    echo ""
    echo "      --single-transaction: the dump was rolled back. $DB_NAME is"
    echo "      unchanged from before this run -- nothing was partially applied."
    echo "      Pre-restore state is also dumped at:"
    echo "        $SAFETY_BACKUP"
    echo "      To reload it explicitly:  $BACK_CMD"
    exit 1
fi
RESTORE_SECS=$(( $(date +%s) - RESTORE_START ))
echo "      psql applied the dump to $DB_NAME with no errors (${RESTORE_SECS}s)"

# --- verify the DATA, not the service ---
# The old script asked `systemctl is-active cas` and called that success. The
# engine starts fine against an empty database, so this asks the only question
# that actually distinguishes a restore from a no-op: is the data there?
# On a production target the services are still down here on purpose -- nothing
# gets traffic until the tables check out. On a drill this is the entire test.
if [ "$IS_PROD_TARGET" -eq 1 ]; then
    step "Verifying restored data in $DB_NAME (services still stopped)..."
else
    step "Verifying restored data in $DB_NAME..."
fi
VERIFY_FAILED=0
for TBL in $VERIFY_TABLES; do
    if ! ROWS=$(count_rows "$TBL"); then
        echo "      FAIL  $TBL — count query failed (see $RESTORE_LOG)"
        VERIFY_FAILED=1
        continue
    fi
    ROWS=$(echo "$ROWS" | tr -d '[:space:]')
    case "$ROWS" in
        ''|*[!0-9]*)
            echo "      FAIL  $TBL — count returned non-numeric result: '${ROWS}'"
            VERIFY_FAILED=1
            continue
            ;;
    esac
    if [ "$ROWS" -eq 0 ]; then
        echo "      FAIL  $TBL — 0 rows (this table is never empty in a real DB)"
        VERIFY_FAILED=1
    else
        echo "      ok    $TBL — $ROWS rows"
    fi
done

if [ "$VERIFY_FAILED" -ne 0 ]; then
    echo ""
    echo "═══════════════════════════════════════════════════════════"
    echo "RESTORE FAILED VERIFICATION  (target: $DB_NAME)"
    echo "═══════════════════════════════════════════════════════════"
    echo "psql reported no errors, but $DB_NAME does not hold the data it must"
    echo "hold."
    if [ "$IS_PROD_TARGET" -eq 1 ]; then
        echo "Services are left STOPPED — nothing is serving this database until"
        echo "you decide what to do."
    fi
    echo ""
    echo "Restore log:   $RESTORE_LOG"
    echo "Safety backup: $SAFETY_BACKUP"
    echo "Go back with:  $BACK_CMD"
    if [ "$IS_PROD_TARGET" -eq 1 ]; then
        echo "Start anyway:  systemctl start cas && systemctl start cas-api"
    fi
    echo "═══════════════════════════════════════════════════════════"
    exit 1
fi

# --- drill ends here: no services to start, no endpoints to check ---
if [ "$IS_PROD_TARGET" -ne 1 ]; then
    echo ""
    echo "═══════════════════════════════════════════════════════════"
    echo "DRILL RESTORE COMPLETED SUCCESSFULLY"
    echo "═══════════════════════════════════════════════════════════"
    echo "Target DB:       $DB_NAME  ($PROD_DB was not touched)"
    echo "Restore time:    ${RESTORE_SECS}s"
    echo "Verified tables: $VERIFY_TABLES (all non-empty)"
    echo "Restore log:     $RESTORE_LOG"
    echo "Safety backup:   $SAFETY_BACKUP"
    echo ""
    echo "This exercised the same code path a production restore takes, minus"
    echo "the service stop/start. The dump is restorable."
    echo "═══════════════════════════════════════════════════════════"
    exit 0
fi

# --- start both services ---
step "Starting services..."
start_services

# --- verify services answer ---
step "Checking endpoints..."
HEALTH_FAILED=0
curl -fsS --max-time 15 http://localhost:8765/health >/dev/null 2>&1 \
    && echo "      ok    engine /health" \
    || { echo "      FAIL  engine /health"; HEALTH_FAILED=1; }
curl -fsS --max-time 15 http://127.0.0.1:8766/api/v2/health >/dev/null 2>&1 \
    && echo "      ok    api /api/v2/health" \
    || { echo "      FAIL  api /api/v2/health"; HEALTH_FAILED=1; }

if [ "$HEALTH_FAILED" -ne 0 ]; then
    echo ""
    echo "═══════════════════════════════════════════════════════════"
    echo "DATA RESTORED, BUT A SERVICE IS NOT ANSWERING"
    echo "═══════════════════════════════════════════════════════════"
    echo "The row checks passed, so $DB_NAME itself is populated. Check:"
    echo "  journalctl -u cas -n 30"
    echo "  journalctl -u cas-api -n 30"
    echo "Restore log:   $RESTORE_LOG"
    echo "Safety backup: $SAFETY_BACKUP"
    echo "═══════════════════════════════════════════════════════════"
    exit 1
fi

echo ""
echo "═══════════════════════════════════════════════════════════"
echo "RESTORE COMPLETED SUCCESSFULLY"
echo "═══════════════════════════════════════════════════════════"
echo "Target DB:       $DB_NAME"
echo "Restore time:    ${RESTORE_SECS}s"
echo "Verified tables: $VERIFY_TABLES (all non-empty)"
echo "Endpoints:       engine :8765 and api :8766 both answering"
echo "Restore log:     $RESTORE_LOG"
echo "Safety backup:   $SAFETY_BACKUP"
echo "  (delete after verifying the system works correctly)"
echo "═══════════════════════════════════════════════════════════"
