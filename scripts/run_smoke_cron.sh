#!/bin/bash
# CAS Smoke Test Cron Wrapper
# Çalıştırma: günlük 04:00 (crontab entry)
# Log: /var/log/cas_smoke.log
# Exit: 0 = OK, !=0 = fail

set -e

LOG_FILE="/var/log/cas_smoke.log"
TIMESTAMP=$(date -Iseconds)

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
        exit 1
    else
        echo "  RESULT: PASS"
        exit 0
    fi
} >> "$LOG_FILE" 2>&1
