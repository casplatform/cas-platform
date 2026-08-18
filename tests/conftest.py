"""pytest config — makes cas_engine importable during collection."""
import sys, os

# The tree these tests live in. Every test module imports this instead of
# naming a path: a literal "/opt/cas" inserted at sys.path[0] by any single
# test module wins over this conftest for the whole session, so the staging
# suite silently imported production's cas_engine and reported it as passing.
INSTANCE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, INSTANCE_ROOT)

# Modules under test resolve their own tree from CAS_HOME (core/paths.py and
# the _CAS_HOME idiom), defaulting to /opt/cas when it is unset. Without this
# the staging suite would import staging code that then reached into
# production's tree for .env and sibling modules. setdefault: an explicit
# CAS_HOME from the caller still wins.
os.environ.setdefault("CAS_HOME", INSTANCE_ROOT)

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

# AuthManager is one of those module-scope objects, and it now refuses to
# construct without AUTH_SECRET rather than defaulting to a random value --
# defaulting is what silently logged every user out on each engine restart.
# Collection therefore needs a secret present for the same reason it needs
# DB_URL: the import happens before any fixture can set one.
#
# A fixed sentinel, not secrets.token_hex(): a random per-run key would make
# any test that signs a token and verifies it in a later assertion depend on
# both halves landing in the same process, which is exactly the kind of
# order-dependent pass this conftest exists to prevent. It is deliberately not
# a plausible key. setdefault, so a caller's real value still wins.
UNIT_TEST_AUTH_SECRET = "test-only-auth-secret-do-not-use-outside-pytest"
os.environ.setdefault("AUTH_SECRET", UNIT_TEST_AUTH_SECRET)

import pytest

@pytest.fixture(autouse=True)
def isolate_db(monkeypatch):
    """Force psycopg2.connect to fail so parse_cdm() falls back to DB-less mode.
    parse_cdm wraps DB call in try/except and degrades gracefully when DB is
    unavailable — we exploit that to keep these as pure unit tests.
    """
    monkeypatch.setenv("DB_URL", UNIT_TEST_DB_URL)
