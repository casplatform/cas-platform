"""pytest config — adds /opt/cas to path so cas_engine can be imported."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

@pytest.fixture(autouse=True)
def isolate_db(monkeypatch):
    """Force psycopg2.connect to fail so parse_cdm() falls back to DB-less mode.
    parse_cdm wraps DB call in try/except and degrades gracefully when DB is
    unavailable — we exploit that to keep these as pure unit tests.
    """
    monkeypatch.setenv("DB_URL", "postgresql://invalid:invalid@127.0.0.1:1/nodb")
