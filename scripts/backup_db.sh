#!/bin/bash
# ═══════════════════════════════════════════════════════════════
# CAS Platform — Automated PostgreSQL Backup
# ═══════════════════════════════════════════════════════════════
# Runs daily via cron. Creates:
#   - Daily backup (always)
#   - Weekly backup (every Sunday, separate copy)
#   - Monthly backup (every 1st of month, separate copy)
#
# Retention:
#   - Daily:   14 days
#   - Weekly:  8 weeks
#   - Monthly: 12 months
# ═══════════════════════════════════════════════════════════════

set -euo pipefail

# === Config ===
BACKUP_ROOT="/opt/cas/backups/db"
LOG_FILE="/var/log/cas/backup.log"
DB_NAME="casdb"
DB_USER="cas"
DB_HOST="localhost"
DB_PORT="5432"

DAILY_RETENTION_DAYS=14
WEEKLY_RETENTION_WEEKS=8
MONTHLY_RETENTION_MONTHS=12

# === Timestamps ===
NOW=$(date +%Y-%m-%d_%H-%M)
DAY_OF_WEEK=$(date +%u)        # 1=Mon..7=Sun
DAY_OF_MONTH=$(date +%d)
WEEK_NUM=$(date +%V)
YEAR=$(date +%Y)
MONTH=$(date +%m)
DAY_NAME=$(date +%a)

# === Logging helper ===
log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$LOG_FILE"
}

# === data-health reporting =====================================
# The 25-26 July 2026 backups were skipped and nobody noticed for two days --
# a backup that does not run produces no output to miss. Reporting into the
# same data_health table the cron feeds use makes a missed backup look like a
# dead feed: stale after 2x the 1440-minute interval, i.e. 48h.
#
# CAS_HOME (not a literal /opt/cas) so a staging run reports into staging's
# data_health, never production's. Reporting is best-effort: a health-tracking
# problem must never turn a good backup into a failed one.
CAS_HOME="${CAS_HOME:-/opt/cas}"
CAS_HOME="${CAS_HOME%/}"

report_health() {
    # $1 = ok | fail, $2 = error message (fail only)
    local _st="$1" _msg="${2:-}"
    CAS_HOME="$CAS_HOME" DH_STATUS="$_st" DH_MSG="$_msg" python3 -c '
import os, sys
sys.path.insert(0, os.path.join(os.environ["CAS_HOME"], "cas_api"))
from core.data_health import report_success, report_failure
if os.environ["DH_STATUS"] == "ok":
    report_success("backup")
else:
    report_failure("backup", os.environ["DH_MSG"])
' >>"$LOG_FILE" 2>&1 \
        || log "WARNING: data_health report ($_st) failed -- see above; the backup itself is unaffected"
}

log "=== Backup started ==="

# === Daily backup (always) ===
DAILY_DIR="$BACKUP_ROOT/daily"
DAILY_FILE="$DAILY_DIR/casdb_${NOW}.sql.gz"

log "Creating daily backup: $DAILY_FILE"
if sudo -u postgres pg_dump "$DB_NAME" \
    --no-owner --no-acl --clean --if-exists \
    | gzip -9 > "$DAILY_FILE"; then
    SIZE=$(du -h "$DAILY_FILE" | cut -f1)
    log "Daily backup OK ($SIZE)"
else
    log "ERROR: Daily backup FAILED"
    report_health fail "pg_dump of $DB_NAME failed; see $LOG_FILE"
    exit 1
fi

# === Verify backup integrity ===
if ! gzip -t "$DAILY_FILE" 2>/dev/null; then
    log "ERROR: Backup file corrupted (gzip test failed)"
    rm -f "$DAILY_FILE"
    report_health fail "gzip integrity test failed for $DAILY_FILE; file removed"
    exit 1
fi

# === Weekly backup (every Sunday) ===
if [ "$DAY_OF_WEEK" = "7" ]; then
    WEEKLY_DIR="$BACKUP_ROOT/weekly"
    WEEKLY_FILE="$WEEKLY_DIR/casdb_${YEAR}-W${WEEK_NUM}_${DAY_NAME}.sql.gz"
    log "Creating weekly backup: $WEEKLY_FILE"
    cp "$DAILY_FILE" "$WEEKLY_FILE"
    log "Weekly backup OK"
fi

# === Monthly backup (every 1st of month) ===
if [ "$DAY_OF_MONTH" = "01" ]; then
    MONTHLY_DIR="$BACKUP_ROOT/monthly"
    MONTHLY_FILE="$MONTHLY_DIR/casdb_${YEAR}-${MONTH}.sql.gz"
    log "Creating monthly backup: $MONTHLY_FILE"
    cp "$DAILY_FILE" "$MONTHLY_FILE"
    log "Monthly backup OK"
fi

# === Retention: cleanup old backups ===
log "Cleaning up old backups..."

# Daily — older than 14 days
DELETED_DAILY=$(find "$BACKUP_ROOT/daily" -name "casdb_*.sql.gz" -type f -mtime +${DAILY_RETENTION_DAYS} -delete -print | wc -l)
log "Deleted $DELETED_DAILY old daily backup(s)"

# Weekly — older than 8 weeks (56 days)
DELETED_WEEKLY=$(find "$BACKUP_ROOT/weekly" -name "casdb_*.sql.gz" -type f -mtime +$((WEEKLY_RETENTION_WEEKS * 7)) -delete -print | wc -l)
log "Deleted $DELETED_WEEKLY old weekly backup(s)"

# Monthly — older than 12 months (~365 days)
DELETED_MONTHLY=$(find "$BACKUP_ROOT/monthly" -name "casdb_*.sql.gz" -type f -mtime +$((MONTHLY_RETENTION_MONTHS * 30)) -delete -print | wc -l)
log "Deleted $DELETED_MONTHLY old monthly backup(s)"

# === Summary ===
DAILY_COUNT=$(find "$BACKUP_ROOT/daily" -name "*.sql.gz" -type f | wc -l)
WEEKLY_COUNT=$(find "$BACKUP_ROOT/weekly" -name "*.sql.gz" -type f | wc -l)
MONTHLY_COUNT=$(find "$BACKUP_ROOT/monthly" -name "*.sql.gz" -type f | wc -l)
TOTAL_SIZE=$(du -sh "$BACKUP_ROOT" | cut -f1)

log "Current backups: $DAILY_COUNT daily, $WEEKLY_COUNT weekly, $MONTHLY_COUNT monthly (total: $TOTAL_SIZE)"
# Reported only here: past this point the dump exists, passed its gzip test,
# and retention has run. report_failure() leaves last_success_at alone, so a
# failed run never overwrites the last known-good backup time.
report_health ok

log "=== Backup completed successfully ==="

exit 0
