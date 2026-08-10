#!/bin/bash
# CAS Smoke Test Cron Wrapper
# Çalıştırma: günlük 04:00 (crontab entry)
# Log: /var/log/cas_smoke.log
# Exit: 0 = OK, !=0 = fail

set -e

LOG_FILE="/var/log/cas_smoke.log"
TIMESTAMP=$(date -Iseconds)

cd /opt/cas

{
    echo ""
    echo "=========================================="
    echo "  SMOKE TEST - $TIMESTAMP"
    echo "=========================================="

    # Local engine smoke (port 8765)
    echo ""
    echo ">>> LOCAL ENGINE SMOKE (http://127.0.0.1:8765)"
    SMOKE_BASE_URL=http://127.0.0.1:8765 python3 -m pytest tests/smoke/ -v --tb=short 2>&1
    LOCAL_EXIT=$?

    # Prod URL smoke (full chain)
    echo ""
    echo ">>> PROD URL SMOKE (https://www.casplatform.com)"
    SMOKE_BASE_URL=https://www.casplatform.com python3 -m pytest tests/smoke/ -v --tb=short 2>&1
    PROD_EXIT=$?

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
