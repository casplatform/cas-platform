#!/bin/bash
# Adds CDM parser test suite + DB isolation fixture
set -e

# 1) Extend conftest.py with DB isolation fixture (don't rewrite, append)
CONFTEST=/opt/cas/tests/conftest.py
cp "$CONFTEST" "/root/nginx_backups/conftest.py.bak.$(date +%s)"

if ! grep -q "isolate_db" "$CONFTEST"; then
cat >> "$CONFTEST" << 'PYEOF'

import pytest

@pytest.fixture(autouse=True)
def isolate_db(monkeypatch):
    """Force psycopg2.connect to fail so parse_cdm() falls back to DB-less mode.
    parse_cdm wraps DB call in try/except and degrades gracefully when DB is
    unavailable — we exploit that to keep these as pure unit tests.
    """
    monkeypatch.setenv("DB_URL", "postgresql://invalid:invalid@127.0.0.1:1/nodb")
PYEOF
  echo "[OK] conftest.py extended with DB isolation fixture"
else
  echo "[SKIP] isolate_db fixture already present"
fi

# 2) Write the new test file
cat > /opt/cas/tests/test_cdm_parser.py << 'PYEOF'
"""
Unit tests for parse_cdm() — Space-Track CDM JSON -> internal dict.

Validates:
- Field fallback chains (MIN_RNG -> MISS_DISTANCE -> MINIMUM_RANGE)
- String->float coercion (Space-Track returns numbers as strings)
- Graceful degradation on missing/malformed fields
- Risk level assignment consistency
- Output schema completeness

These tests lock down the ingestion boundary: any upstream schema change
in Space-Track CDM format will surface here before affecting production.
"""
import pytest
from cas_engine import parse_cdm


def _minimal_cdm(**overrides):
    """Build a minimal valid CDM dict, allow per-test overrides."""
    base = {
        "CDM_ID": "TEST-001",
        "SAT_1_NAME": "SAT ALPHA",
        "SAT_2_NAME": "SAT BETA",
        "SAT_1_ID": "25544",
        "SAT_2_ID": "48274",
        "TCA": "2026-04-15T12:30:00.000",
        "MIN_RNG": "150.0",
        "PC": "0.00005",
        "RELATIVE_SPEED": "14500.0",
    }
    base.update(overrides)
    return base


class TestParseCDMHappyPath:
    """Typical Space-Track CDM should parse cleanly."""

    def test_returns_dict(self):
        result = parse_cdm(_minimal_cdm())
        assert isinstance(result, dict)

    def test_schema_completeness(self):
        # 17 expected output fields per the function contract
        result = parse_cdm(_minimal_cdm())
        expected = {
            "cdm_id", "sat1", "sat2", "norad1", "norad2",
            "tca_str", "tca_hours", "miss_distance_m", "miss_distance_km",
            "relative_velocity_ms", "Pc", "Pc_str", "risk", "maneuver",
            "emergency_reportable", "sat1_type", "sat2_type", "source",
        }
        assert expected.issubset(result.keys()), \
            f"missing: {expected - set(result.keys())}"

    def test_source_tagged(self):
        # Every parsed CDM should be tagged with its provenance
        assert parse_cdm(_minimal_cdm())["source"] == "Space-Track CDM"


class TestFieldFallbacks:
    """parse_cdm must tolerate Space-Track schema variants."""

    def test_miss_distance_fallback_chain(self):
        # Priority: MIN_RNG > MISS_DISTANCE > MINIMUM_RANGE
        r1 = parse_cdm(_minimal_cdm(MIN_RNG="100", MISS_DISTANCE="200"))
        assert r1["miss_distance_m"] == 100.0
        cdm = _minimal_cdm()
        cdm.pop("MIN_RNG")
        cdm["MISS_DISTANCE"] = "250"
        assert parse_cdm(cdm)["miss_distance_m"] == 250.0
        cdm2 = {k: v for k, v in _minimal_cdm().items() if k != "MIN_RNG"}
        cdm2["MINIMUM_RANGE"] = "333"
        assert parse_cdm(cdm2)["miss_distance_m"] == 333.0

    def test_pc_field_alias(self):
        # Both PC and COLLISION_PROBABILITY should work
        cdm = _minimal_cdm()
        cdm.pop("PC")
        cdm["COLLISION_PROBABILITY"] = "0.001"
        assert parse_cdm(cdm)["Pc"] == pytest.approx(0.001)

    def test_sat_name_alias(self):
        cdm = _minimal_cdm()
        cdm.pop("SAT_1_NAME")
        cdm.pop("SAT_2_NAME")
        cdm["SAT1_NAME"] = "ALPHA-ALT"
        cdm["SAT2_NAME"] = "BETA-ALT"
        result = parse_cdm(cdm)
        assert result["sat1"] == "ALPHA-ALT"
        assert result["sat2"] == "BETA-ALT"


class TestTypeCoercion:
    """Space-Track returns numeric fields as strings — must coerce to float."""

    def test_string_pc_coerced(self):
        result = parse_cdm(_minimal_cdm(PC="0.000123"))
        assert isinstance(result["Pc"], float)
        assert result["Pc"] == pytest.approx(0.000123)

    def test_string_miss_distance_coerced(self):
        result = parse_cdm(_minimal_cdm(MIN_RNG="456.7"))
        assert result["miss_distance_m"] == 456.7

    def test_invalid_pc_defaults_to_zero(self):
        # Malformed Pc string must not crash parser
        result = parse_cdm(_minimal_cdm(PC="not-a-number"))
        assert result["Pc"] == 0.0


class TestMissingAndMalformed:
    """Parser must never raise on incomplete CDMs."""

    def test_empty_cdm_graceful(self):
        # Bare minimum: parser returns zeros/placeholders, no exception
        result = parse_cdm({})
        assert result["miss_distance_m"] == 0.0
        assert result["Pc"] == 0.0
        assert result["sat1"] == "UNKNOWN"
        assert result["sat2"] == "UNKNOWN"

    def test_null_pc_treated_as_zero(self):
        result = parse_cdm(_minimal_cdm(PC=None))
        assert result["Pc"] == 0.0


class TestRiskIntegration:
    """parse_cdm must consistently invoke risk_level() and propagate result."""

    def test_high_pc_yields_red_risk(self):
        # Pc > 1e-4 must be labeled RED (per risk_level() thresholds)
        result = parse_cdm(_minimal_cdm(PC="0.001", MIN_RNG="500"))
        assert result["risk"] == "RED"

    def test_very_close_miss_yields_red_risk(self):
        # miss < 200m forces RED even with low Pc
        result = parse_cdm(_minimal_cdm(PC="0", MIN_RNG="50"))
        assert result["risk"] == "RED"

    def test_pc_str_format(self):
        # Pc_str must be scientific notation with 3 decimal places
        result = parse_cdm(_minimal_cdm(PC="0.000456"))
        assert "e" in result["Pc_str"].lower()
        assert result["Pc_str"] == "4.560e-04"
PYEOF
echo "[OK] test_cdm_parser.py written"

# 3) Run the new suite
echo ""
echo "=== Running new CDM parser tests ==="
cd /opt/cas && python3 -m pytest tests/test_cdm_parser.py -v --tb=short

echo ""
echo "=== Running full suite ==="
cd /opt/cas && python3 -m pytest tests/ -v --tb=short 2>&1 | tail -25
