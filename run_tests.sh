#!/bin/bash
# CAS Test Runner
# Usage: bash run_tests.sh [unit|integration|smoke|all]

# Test the tree this script lives in, not a hard-coded /opt/cas. Running the
# staging copy used to cd into production and report on production's files.
cd "$(dirname "$(readlink -f "$0")")"

MODE="${1:-all}"
EXIT_CODE=0

run_unit() {
    echo ""
    echo "═══════════════════════════════════════════"
    echo "  UNIT TESTS (algorithmic core)"
    echo "═══════════════════════════════════════════"
    python3 -m pytest tests/ -q --ignore=tests/integration --ignore=tests/smoke 2>&1
    return $?
}

run_integration() {
    echo ""
    echo "═══════════════════════════════════════════"
    echo "  INTEGRATION TESTS (engine + DB)"
    echo "═══════════════════════════════════════════"
    if [ ! -d "tests/integration" ]; then
        echo "  [SKIP] tests/integration/ yok"
        return 0
    fi
    python3 -m pytest tests/integration/ -v 2>&1
    return $?
}

run_smoke() {
    echo ""
    echo "═══════════════════════════════════════════"
    echo "  SMOKE TESTS (production endpoints)"
    echo "═══════════════════════════════════════════"
    if [ ! -d "tests/smoke" ]; then
        echo "  [SKIP] tests/smoke/ yok (Sprint 1'de eklenmedi)"
        return 0
    fi
    python3 -m pytest tests/smoke/ -v 2>&1
    return $?
}

case "$MODE" in
    unit)
        run_unit
        EXIT_CODE=$?
        ;;
    integration)
        run_integration
        EXIT_CODE=$?
        ;;
    smoke)
        run_smoke
        EXIT_CODE=$?
        ;;
    all)
        run_unit
        U=$?
        run_integration
        I=$?
        run_smoke
        S=$?
        echo ""
        echo "═══════════════════════════════════════════"
        echo "  ÖZET"
        echo "═══════════════════════════════════════════"
        [ $U -eq 0 ] && echo "  Unit:        ✓ PASS" || echo "  Unit:        ✗ FAIL"
        [ $I -eq 0 ] && echo "  Integration: ✓ PASS" || echo "  Integration: ✗ FAIL"
        [ $S -eq 0 ] && echo "  Smoke:       ✓ PASS" || echo "  Smoke:       ✗ FAIL"
        echo ""
        EXIT_CODE=$(( U + I + S ))
        ;;
    *)
        echo "Usage: $0 [unit|integration|smoke|all]"
        exit 1
        ;;
esac

exit $EXIT_CODE
