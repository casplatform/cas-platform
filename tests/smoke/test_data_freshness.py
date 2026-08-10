"""Upstream data-flow checks against the live production database.

These are not integration tests: they assert nothing about code behaviour.
They check that the ingestion pipelines have actually delivered data, which
is a property of the running system rather than of the codebase. They were
moved out of tests/integration/ when integration tests were isolated onto a
dedicated test database, where "has the cron run?" is meaningless.

Skipped automatically when the production database is unreachable.
"""
import os
import pytest
import psycopg2


def _prod_url():
    """Always read from .env, never from the environment.

    The integration conftest rewrites os.environ['DB_URL'] to the test database
    at import time, and pytest loads every conftest during collection — so by
    the time these tests run, the environment points at casdb_test. These checks
    are about the production system specifically, so they resolve the URL from
    the deployment's .env directly.
    """
    env = "/opt/cas/.env"
    if not os.path.exists(env):
        return ""
    for line in open(env):
        line = line.strip()
        if line.startswith("DB_URL=") and "=" in line:
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    return ""


@pytest.fixture(scope="module")
def prod_conn():
    url = _prod_url()
    if not url:
        pytest.skip("production DB_URL bulunamadi")
    try:
        conn = psycopg2.connect(url)
    except Exception as e:
        pytest.skip("production veritabanina baglanilamadi: %s" % e)
    yield conn
    conn.close()


def _count(conn, sql):
    cur = conn.cursor()
    cur.execute(sql)
    n = cur.fetchone()[0]
    cur.close()
    return n


def test_conjunction_events_populated(prod_conn):
    """fetch_cdm calismis ve conjunction verisi gelmis olmali."""
    n = _count(prod_conn, "SELECT count(*) FROM conjunction_events")
    assert n >= 1, "conjunction_events bos - fetch_cdm calismamis olabilir"


def test_eusst_fragmentation_populated(prod_conn):
    n = _count(prod_conn, "SELECT count(*) FROM eusst_fg_events")
    assert n >= 1, "Hic FG event yok - EU SST sync calismamis olabilir"


def test_eusst_reentry_populated(prod_conn):
    n = _count(prod_conn, "SELECT count(*) FROM eusst_re_events")
    assert n >= 1, "Hic RE event yok - EU SST sync calismamis olabilir"


def test_eusst_sync_state_both_services(prod_conn):
    """fg ve re icin ayri sync_state kaydi tutulmali (artimli senkron)."""
    cur = prod_conn.cursor()
    cur.execute("SELECT service FROM eusst_sync_state ORDER BY service")
    services = [r[0] for r in cur.fetchall()]
    cur.close()
    assert "fg" in services, "fg sync_state kaydi yok"
    assert "re" in services, "re sync_state kaydi yok"
