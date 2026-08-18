"""pytest config — makes cas_engine importable during collection."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# cas_engine builds AUTH, WATCHLIST, ADMIN and DECISION at module scope, and
# each reads os.environ["DB_URL"] in __init__. That happens when a test module
# imports it -- during collection, before any fixture runs. The autouse fixture
# below therefore cannot prevent a KeyError at import; it only rebinds the value
# once collection has already succeeded.
#
# Five unit-test modules that import cas_engine crashed when run on their own
# and passed in the full suite, because tests/integration/conftest.py sets
# DB_URL at module scope and is collected first. Tests that pass only in a
# particular order are not much of a gate, so the default is set here, before
# any import can need it.
#
# setdefault, not a plain assignment: a caller who has already exported DB_URL
# keeps it. Note this guards DB_URL only -- TEST_DB_URL is a separate variable
# (the deploy script passes that one) and is read by tests/integration/conftest.py,
# which never derives a test database from the sentinel below.
UNIT_TEST_DB_URL = "postgresql://invalid:invalid@127.0.0.1:1/nodb"
os.environ.setdefault("DB_URL", UNIT_TEST_DB_URL)

import pytest

@pytest.fixture(autouse=True)
def isolate_db(monkeypatch):
    """Force psycopg2.connect to fail so parse_cdm() falls back to DB-less mode.
    parse_cdm wraps DB call in try/except and degrades gracefully when DB is
    unavailable — we exploit that to keep these as pure unit tests.
    """
    monkeypatch.setenv("DB_URL", UNIT_TEST_DB_URL)
