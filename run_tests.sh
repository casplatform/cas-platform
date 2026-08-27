#!/bin/bash
# CAS Test Runner
# Usage: bash run_tests.sh [unit|integration|smoke|all]

# Test the tree this script lives in, not a hard-coded /opt/cas. Running the
# staging copy used to cd into production and report on production's files.
cd "$(dirname "$(readlink -f "$0")")"

# This instance's interpreter, not python3. Since the venv migration the system
# python and the venvs resolve 6 of the 58 constrained packages differently
# (starlette and PyJWT among them -- the request layer and the token layer), so
# `python3 -m pytest` here reported on a set of versions no instance runs. That
# is the same silent divergence deploy.sh's test gate was changed to remove;
# this runner had been left behind on the old side of it.
PY="$(pwd)/.venv/bin/python"
[ -x "$PY" ] || { echo "no interpreter at $PY -- build it with: python3 -m venv .venv"; exit 2; }
# Production's venv is built from requirements.txt alone and deliberately has no
# pytest: it holds what the services import while serving traffic, and a test
# framework there would be a package production carries and never imports. So
# this runner is a staging/dev tool by construction. Say so, rather than falling
# back to python3 and quietly testing versions nothing runs.
"$PY" -c "import pytest" 2>/dev/null || {
    echo "$PY has no pytest."
    echo "Expected in production: that venv is built from requirements.txt only."
    echo "Run the suite from /opt/cas_staging instead."
    exit 2
}

MODE="${1:-all}"
EXIT_CODE=0

run_unit() {
    echo ""
    echo "═══════════════════════════════════════════"
    echo "  UNIT TESTS (algorithmic core)"
    echo "═══════════════════════════════════════════"
    "$PY" -m pytest tests/ -q --ignore=tests/integration --ignore=tests/smoke 2>&1
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
    "$PY" -m pytest tests/integration/ -v 2>&1
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
    "$PY" -m pytest tests/smoke/ -v 2>&1
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
