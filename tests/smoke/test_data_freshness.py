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


def _age_hours(conn, table, column):
    """Hours since the newest row, or None when the table is empty."""
    cur = conn.cursor()
    cur.execute("SELECT EXTRACT(EPOCH FROM (now() - MAX({0})))/3600 FROM {1}".format(
        column, table))
    row = cur.fetchone()
    cur.close()
    return float(row[0]) if row and row[0] is not None else None


# ── Freshness, not existence ─────────────────────────────────────────────
#
# These three used to assert count(*) >= 1. conjunction_events holds 41,000
# rows, so that test passed whether fetch_cdm had run this morning or died a
# year ago -- the same "an empty result read as a clean result" mistake
# CLAUDE.md warns about, made in the monitoring layer itself.
#
# Every threshold below is measured rather than guessed, and every one is
# deliberately loose. These run against production once a day: a false failure
# costs more than a few hours of detection delay, because a check that cries
# wolf is a check nobody reads.

def test_conjunction_events_fresh(prod_conn, production_only):
    """fetch_cdm son bir gun icinde veri yazmis olmali.

    Cadence measured 2026-08-27 over 30 days of fetched_at: 80 of 85 gaps are
    exactly 8h (the 00:00/08:00/16:00 cron), the largest is 15h. 36h is four
    and a half missed runs -- comfortably past any single hiccup, and far
    below the year this test used to tolerate.
    """
    assert _count(prod_conn, "SELECT count(*) FROM conjunction_events") >= 1, \
        "conjunction_events bos"
    age = _age_hours(prod_conn, "conjunction_events", "fetched_at")
    assert age is not None and age < 36, (
        "En yeni CDM %.1f saatlik - fetch_cdm durmus olabilir "
        "(beklenen aralik 8h, olculen en buyuk bosluk 15h)" % (age or -1))


def test_eusst_sync_ran_recently(prod_conn, production_only):
    """EU SST senkronu son bir gun icinde kosmus olmali.

    NOT on event age. Measured 2026-08-27 over 12 months of update_date, EU SST
    publishes reentries with a median gap of 5 days (p95 13, max 15) and
    fragmentations with a median of 33 days (max 72). An assertion on event age
    would be an assertion about how busy Europe's reentry season is, which is
    not a property of this system. eusst_sync_state.last_sync_at is when OUR
    job last ran, which is. Cron is every 6h; 24h is four missed runs.
    """
    cur = prod_conn.cursor()
    cur.execute("SELECT service, EXTRACT(EPOCH FROM (now() - last_sync_at))/3600 "
                "FROM eusst_sync_state ORDER BY service")
    rows = cur.fetchall()
    cur.close()
    assert rows, "eusst_sync_state bos - senkron hic kosmamis"
    stale = ["%s: %.1fh" % (svc, hrs) for svc, hrs in rows if hrs is None or hrs > 24]
    assert not stale, (
        "EU SST senkronu bayat (%s) - cron 6 saatte bir kosmali" % ", ".join(stale))


def test_eusst_tables_populated(prod_conn, production_only):
    """Iki olay tablosu da dolu olmali.

    Kept as a structural check and nothing more: these tables are an archive,
    so their row count only ever goes up and cannot say anything about today.
    Freshness is the test above.
    """
    assert _count(prod_conn, "SELECT count(*) FROM eusst_fg_events") >= 1, \
        "Hic FG event yok"
    assert _count(prod_conn, "SELECT count(*) FROM eusst_re_events") >= 1, \
        "Hic RE event yok"


def test_space_weather_fresh(prod_conn, production_only):
    """NOAA anlik goruntusu saatlik gelmeli.

    Cron is `15 * * * *`. Measured over the last 7 days: 167 consecutive hourly
    gaps, maximum 1.0h -- the tightest cadence we have. 6h is six missed runs.
    """
    age = _age_hours(prod_conn, "space_weather_snapshots", "fetched_at")
    assert age is not None and age < 6, (
        "En yeni space-weather kaydi %.1f saatlik - saatlik cron durmus olabilir"
        % (age or -1))


def test_eusst_sync_state_both_services(prod_conn, production_only):
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


def test_catalog_cache_recently_fetched(production_only):
    """Katalog cache'i son 7 gun icinde tazelenmis olmali (sync yasiyor mu)."""
    if not os.path.exists(_PROD_CACHE):
        pytest.skip("production catalog cache yok: %s" % _PROD_CACHE)
    with open(_PROD_CACHE) as f:
        cache = json.load(f)
    fetched_at = cache.get("fetched_at", 0)
    age_days = (time.time() - fetched_at) / 86400
    assert age_days < 7, (
        "Cache %.1f gunluk - catalog sync durmus olabilir" % age_days)


# ── The health endpoint itself ───────────────────────────────────────────
#
# Until now no smoke test touched /health, /health/detailed, /health/sources or
# /metrics. That is why /health/detailed spent twelve hours of every day
# returning 503 without anyone noticing: the endpoint had no reader, human or
# automated. One reader is enough to change that.

def test_health_sources_shape(smoke_get):
    """Uc cevap veriyor ve her kaynak durumunu bildiriyor.

    The code-shaped half: this runs against whichever instance is under test,
    because "does the endpoint still work" is a property of the commit. It is
    what would catch the endpoint breaking outright -- which is how it spent
    its whole life until 2026-08-27, unread by any test.
    """
    r = smoke_get("/health/sources")
    assert r.status_code == 200, "/health/sources HTTP %s" % r.status_code
    srcs = r.json().get("sources") or {}
    assert srcs, "/health/sources bos dondu - data_health okunamiyor"
    missing = sorted(k for k, v in srcs.items() if "status" not in v)
    assert not missing, "durum alani olmayan kaynaklar: %s" % ", ".join(missing)


def test_health_sources_none_stale(smoke_get, production_only):
    """Hicbir musteri-yuzlu kaynak bayat olmamali."""
    r = smoke_get("/health/sources")
    assert r.status_code == 200, "/health/sources HTTP %s" % r.status_code
    srcs = r.json().get("sources") or {}
    assert srcs, "/health/sources bos dondu - data_health okunamiyor"

    # A source with no row has never reported once. That is the normal state
    # for the minutes between deploying a new source and its first cron run,
    # so it is reported rather than failed on -- naming it keeps it visible
    # without turning every deploy into a red smoke run.
    # "unknown" is waiting for a first run and is fine; "never_ran" is two
    # intervals past that and is caught by the staleness assertion below.
    never = sorted(k for k, v in srcs.items() if v.get("status") == "unknown")
    stale = sorted("%s (%s dk)" % (k, v.get("minutes_stale"))
                   for k, v in srcs.items()
                   if v.get("last_success_at") and v.get("is_stale"))
    if never:
        print("[smoke] henuz hic rapor etmemis kaynaklar: %s" % ", ".join(never))
    assert not stale, "Bayat kaynak: %s" % ", ".join(stale)


def test_health_detailed_thresholds_follow_data_health(smoke_get):
    """Bilesen durumu data_health ile celismemeli.

    The code-shaped half, and the one that pins the bug this test was written
    for. Until 2026-08-27 /health/detailed timed the newest ROW in a table and
    called the age a fault of ours, with thresholds written for an hourly CDM
    cron that had become a three-times-daily one. The result was twelve hours
    of 503 every day on a healthy system -- and, at the same moment,
    /health/sources reporting the very same feed as ok. Two endpoints, opposite
    answers about one event.

    So the assertion is agreement, not health: whatever /health/sources says
    about a feed, /health/detailed must not contradict it. That holds on any
    instance, including staging where every feed is legitimately stale, which
    is what lets this run inside the deploy gate.
    """
    det = smoke_get("/health/detailed")
    assert det.status_code in (200, 503), "/health/detailed HTTP %s" % det.status_code
    src = smoke_get("/health/sources")
    assert src.status_code == 200, "/health/sources HTTP %s" % src.status_code

    sources = src.json().get("sources") or {}
    comps = det.json().get("components") or {}
    # component -> the data_health source it must agree with
    pairs = {"space_track": "cdm", "eu_sst": "eusst", "noaa_swpc": "space_weather"}
    disagree = []
    for comp, source in pairs.items():
        c, h = comps.get(comp), sources.get(source)
        if not c or not h:
            continue
        comp_bad = c.get("status") == "error"
        # /health/sources reports the effective status: "stale" when nothing has
        # reported for longer than the source allows, "failed" when the last
        # attempt failed. Comparing against the raw latch would make this test
        # agree with a dead pipeline.
        src_bad = (h.get("status") in ("failed", "stale", "never_ran")
                   or bool(h.get("is_stale")))
        if comp_bad != src_bad:
            disagree.append("%s=%s ama %s status=%s (reported=%s) is_stale=%s"
                            % (comp, c.get("status"), source, h.get("status"),
                               h.get("reported_status"), h.get("is_stale")))
    assert not disagree, "iki uc ayni olay hakkinda celisiyor: " + "; ".join(disagree)


def test_health_detailed_not_error(smoke_get, production_only):
    """Canli kurulumda hicbir bilesen error olmamali."""
    r = smoke_get("/health/detailed")
    assert r.status_code == 200, (
        "/health/detailed HTTP %s: %s" % (r.status_code, r.text[:300]))
    body = r.json()
    bad = {k: v.get("status") for k, v in (body.get("components") or {}).items()
           if v.get("status") == "error"}
    assert not bad, "error durumundaki bilesenler: %s" % bad


# ── Deprecated routes still answering ────────────────────────────────────
#
# ADR 0001 de-duplicates two surfaces without deleting the losing side in the
# same release. The engine's /api/notification-prefs is no longer called by
# anything -- portal.html now speaks only to /api/v2/notifications/prefs -- but
# it keeps serving until a later commit removes it.
#
# This test exists for the window in between. Its job is not to bless the
# duplication; it is to prove the rollback path is real: if the FastAPI route
# turns out to be wrong, pointing portal.html back at the engine has to work,
# and that is only true while the engine route still answers. When the engine
# route is deleted, delete this test in the same commit.

def test_deprecated_notification_prefs_still_answers(smoke_get):
    """Motorun eski ucu hâlâ cevap vermeli (geri donus yolu canli mi).

    NOTE THE DOUBLED PREFIX, it is not a typo. nginx rewrites `^/api/(.*)` to
    `/$1` before proxying, while this engine route is defined as the literal
    "/api/notification-prefs" -- so reaching it through nginx takes
    /api/api/notification-prefs, which is exactly what portal.html sent
    (`API + '/api/notification-prefs'` with `const API = '/api'`). The smoke
    conftest applies the same strip for the direct-to-engine target, so one
    path works for both. Measured 2026-09-02: /api/notification-prefs through
    nginx is a 404, /api/api/notification-prefs is a 401.
    """
    r = smoke_get("/api/api/notification-prefs")
    # 401 without a token is the point: the route is still routed and still
    # refuses anonymous callers, rather than 404 (deleted) or 5xx (broken).
    assert r.status_code in (401, 503), (
        "engine /api/notification-prefs HTTP %s -- beklenen 401. 404 ise uc "
        "silinmis demektir: bu testi de ayni commit'te kaldirin (ADR 0001)."
        % r.status_code)


def test_notification_prefs_canonical_route_answers(smoke_get, base_url):
    """Kanonik uc (FastAPI) cevap vermeli.

    /api/v2 is served by cas-api on 8766 and only reachable through nginx, so
    this is skipped when the suite is pointed straight at an engine port --
    which is what both the deploy gate (:8775) and the local smoke run (:8765)
    do. It is not a production_only check: it is about the code, and it runs
    wherever the full chain is in front of it.
    """
    if ":8765" in base_url or ":8775" in base_url:
        pytest.skip("dogrudan motor hedefi: /api/v2 nginx arkasinda, bu hedeften gorunmuyor")
    r = smoke_get("/api/v2/notifications/prefs")
    assert r.status_code in (401, 403), (
        "/api/v2/notifications/prefs HTTP %s -- beklenen 401/403" % r.status_code)
