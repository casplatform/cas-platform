#!/bin/bash
# ═══════════════════════════════════════════════════════════════
# CAS Platform — PostgreSQL Restore Script
# ═══════════════════════════════════════════════════════════════
# Usage:
#   ./restore_db.sh <backup_file.sql.gz>
#   ./restore_db.sh --latest
#   ./restore_db.sh --list
# ═══════════════════════════════════════════════════════════════

set -euo pipefail

BACKUP_ROOT="/opt/cas/backups/db"
DB_NAME="casdb"

usage() {
    echo "Usage:"
    echo "  $0 <backup_file.sql.gz>   # restore from specific file"
    echo "  $0 --latest               # restore from most recent daily backup"
    echo "  $0 --list                 # show available backups"
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

# --- confirmation prompt ---
SIZE=$(du -h "$BACKUP_FILE" | cut -f1)
echo "═══════════════════════════════════════════════════════════"
echo "WARNING: This will REPLACE the current database!"
echo "═══════════════════════════════════════════════════════════"
echo "Database:    $DB_NAME"
echo "Backup file: $BACKUP_FILE"
echo "Backup size: $SIZE"
echo "Backup date: $(stat -c %y "$BACKUP_FILE" | cut -d. -f1)"
echo "═══════════════════════════════════════════════════════════"
echo ""
read -p "Type 'YES' to proceed with restore: " CONFIRM
if [ "$CONFIRM" != "YES" ]; then
    echo "Restore cancelled."
    exit 0
fi

# --- pre-restore safety backup ---
SAFETY_BACKUP="/tmp/casdb_safety_$(date +%Y%m%d_%H%M%S).sql.gz"
echo ""
echo "[1/4] Creating safety backup of current DB at: $SAFETY_BACKUP"
sudo -u postgres pg_dump "$DB_NAME" --no-owner --no-acl --clean --if-exists | gzip > "$SAFETY_BACKUP"
echo "      Safety backup OK ($(du -h "$SAFETY_BACKUP" | cut -f1))"

# --- stop CAS service ---
echo ""
echo "[2/4] Stopping cas.service..."
sudo systemctl stop cas
sleep 2

# --- restore ---
echo ""
echo "[3/4] Restoring database from $BACKUP_FILE..."
gunzip -c "$BACKUP_FILE" | sudo -u postgres psql "$DB_NAME" > /dev/null 2>&1 || {
    echo "ERROR: Restore failed. Starting cas.service back up..."
    sudo systemctl start cas
    echo "Safety backup available at: $SAFETY_BACKUP"
    exit 1
}

# --- start CAS service ---
echo ""
echo "[4/4] Starting cas.service..."
sudo systemctl start cas
sleep 3

# --- verify service ---
if sudo systemctl is-active --quiet cas; then
    echo ""
    echo "═══════════════════════════════════════════════════════════"
    echo "RESTORE COMPLETED SUCCESSFULLY"
    echo "═══════════════════════════════════════════════════════════"
    echo "Service status: ACTIVE"
    echo "Safety backup:  $SAFETY_BACKUP"
    echo "  (delete after verifying the system works correctly)"
    echo "═══════════════════════════════════════════════════════════"
else
    echo ""
    echo "WARNING: cas.service did not start cleanly. Check logs:"
    echo "  sudo journalctl -u cas -n 30"
    exit 1
fi
