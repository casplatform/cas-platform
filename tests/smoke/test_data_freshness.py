"""Upstream data-flow checks against the live production database.

These are not integration tests: they assert nothing about code behaviour.
They check that the ingestion pipelines have actually delivered data, which
is a property of the running system rather than of the codebase. They were
moved out of tests/integration/ when integration tests were isolated onto a
dedicated test database, where "has the cron run?" is meaningless.

Skipped automatically when the production database is unreachable.
"""
import json
import os
import time

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


# ── Space-Track katalog cache'i ──────────────────────────────────────────
# Tablo degil dosya, ama sordugu soru bu modulun var olma sebebiyle ayni:
# ingestion gercekten kostu mu?
#
# Bu test tests/integration/test_catalog.py icindeydi ve orada sabit
# "/opt/cas/..." yolunu okuyordu -- yani hangi instance'tan kosarsa kossun
# production'in dosyasi hakkinda rapor veriyordu. O sabit CAS_HOME'a
# cevrilince (dogrusu buydu) test staging'in kendi kopyasini tarif etmeye
# basladi, ve staging'in kopyasi tanim geregi donmus: staging'de sync cron'u
# yok (izolasyon karari, ayrica Space-Track kotasi), dosya oraya elle
# kopyalaniyor. "Taze mi" sorusu yalnizca dosyayi YAZAN instance icin
# anlamli, o da production.
#
# Cache'i deploy sirasinda ya da staging'e ozel bir cron ile production'dan
# kopyalamak da dusunuldu; ikisi de reddedildi. Deploy'un veri yonunu
# tersine cevirir (bugun yalnizca kod staging'den production'a gider) ya da
# elle kontrol edilmesi kararlastirilmis bir instance'a arka plan isi ekler,
# ve her ikisi de bayat bir cache'i KOD kapisinin hatasina donusturur:
# deploy, commit'le ilgisi olmayan bir sebeple bloke olurdu.
_PROD_CACHE = "/opt/cas/.spacetrack_catalog_cache.json"


def test_catalog_cache_recently_fetched():
    """Katalog cache'i son 7 gun icinde tazelenmis olmali (sync yasiyor mu)."""
    if not os.path.exists(_PROD_CACHE):
        pytest.skip("production catalog cache yok: %s" % _PROD_CACHE)
    with open(_PROD_CACHE) as f:
        cache = json.load(f)
    fetched_at = cache.get("fetched_at", 0)
    age_days = (time.time() - fetched_at) / 86400
    assert age_days < 7, (
        "Cache %.1f gunluk - catalog sync durmus olabilir" % age_days)
