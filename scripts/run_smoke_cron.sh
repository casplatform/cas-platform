#!/bin/bash
# CAS Smoke Test Cron Wrapper
# Çalıştırma: günlük 04:00 (crontab entry)
# Log: /var/log/cas_smoke.log
# Exit: 0 = OK, !=0 = fail

set -e

LOG_FILE="/var/log/cas_smoke.log"
TIMESTAMP=$(date -Iseconds)

# Where a failure goes. Everything below is redirected into $LOG_FILE, so cron
# never sees this script's output -- and even if it did, this host runs no MTA,
# so there is nobody for cron to mail. That is why 58 recorded runs contain zero
# failures worth acting on: not because nothing ever broke, but because a break
# had no route out of the log file. data_health already owns the one route that
# works (direct SMTP, one mail on the first failure and one on recovery), so the
# result goes there. Same shape as scripts/backup_db.sh.
CAS_HOME="${CAS_HOME:-/opt/cas}"
report_health() {
    # $1 = ok | fail, $2 = message (fail only)
    CAS_HOME="$CAS_HOME" DH_STATUS="$1" DH_MSG="${2:-}" python3 -c '
import os, sys
sys.path.insert(0, os.path.join(os.environ["CAS_HOME"], "cas_api"))
from core.data_health import report_success, report_failure
if os.environ["DH_STATUS"] == "ok":
    report_success("smoke")
else:
    report_failure("smoke", os.environ["DH_MSG"])
' 2>&1 \
        || echo "WARNING: data_health report ($1) failed -- the smoke result above still stands"
}

# Run the suite from the tree this script lives in, so the staging copy does
# not silently smoke-test production's files.
cd "$(dirname "$(dirname "$(readlink -f "$0")")")"

{
    echo ""
    echo "=========================================="
    echo "  SMOKE TEST - $TIMESTAMP"
    echo "=========================================="

    # Local engine smoke (port 8765)
    echo ""
    echo ">>> LOCAL ENGINE SMOKE (http://127.0.0.1:8765)"
    # `|| LOCAL_EXIT=$?` is required, not stylistic. Under `set -e` a failing
    # pytest aborts the shell immediately, so the bare `LOCAL_EXIT=$?` that used
    # to be here was never reached and everything below -- the second suite, the
    # SUMMARY block, the FAIL result -- was unreachable code. The cron job could
    # only ever log a pass. Putting pytest on the left of `||` also suspends
    # errexit for it, which is exactly what we want: a failing suite must be
    # recorded, not fatal.
    LOCAL_EXIT=0
    SMOKE_BASE_URL=http://127.0.0.1:8765 python3 -m pytest tests/smoke/ -v --tb=short 2>&1 || LOCAL_EXIT=$?

    # Prod URL smoke (full chain)
    echo ""
    echo ">>> PROD URL SMOKE (https://www.casplatform.com)"
    PROD_EXIT=0
    SMOKE_BASE_URL=https://www.casplatform.com python3 -m pytest tests/smoke/ -v --tb=short 2>&1 || PROD_EXIT=$?

    echo ""
    echo ">>> SUMMARY"
    [ $LOCAL_EXIT -eq 0 ] && echo "  Local: PASS" || echo "  Local: FAIL"
    [ $PROD_EXIT -eq 0 ] && echo "  Prod:  PASS" || echo "  Prod:  FAIL"

    # Toplam exit code (her ikisinin OR'u)
    if [ $LOCAL_EXIT -ne 0 ] || [ $PROD_EXIT -ne 0 ]; then
        echo "  RESULT: FAIL"
        report_health fail "local pytest exit=$LOCAL_EXIT, prod URL pytest exit=$PROD_EXIT -- see $LOG_FILE"
        exit 1
    else
        echo "  RESULT: PASS"
        report_health ok
        exit 0
    fi
} >> "$LOG_FILE" 2>&1
