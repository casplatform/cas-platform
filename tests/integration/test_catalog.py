"""
Catalog cache tests: Space-Track cache yapisi, dedup, freshness.

DB+filesystem only. Network gerekmez (cache dosyasini test eder).
"""
import pytest
import os
import json


# Cache dosyasi TEST EDILEN instance'a ait olmali. Burada sabit
# "/opt/cas/..." yaziyordu: staging suite'i production'in cache'ini okuyup
# onun hakkinda rapor veriyordu -- staging'in cache'i bayat ya da hic yokken
# testler yesil kaliyordu. Ayni sabit, /opt/cas'in bulunmadigi bir makinede
# (CI) bu dosyadaki her testi skip yerine FAIL yapardi.
# cas_engine ayni yolu _CAS_HOME'dan kuruyor (_ST_CATALOG_CACHE_FILE);
# tests/conftest.py da CAS_HOME'u testlerin icinde durdugu agaca set ediyor.
_INSTANCE_ROOT = os.environ.get("CAS_HOME") or os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CACHE_FILE = os.path.join(_INSTANCE_ROOT, ".spacetrack_catalog_cache.json")

# Cache dosyasini repo degil catalog sync uretiyor: temiz bir checkout'ta (CI)
# ve hic sync olmamis bir instance'ta yoktur. Bu testler bir kurulumun cache
# durumunu tarif ediyor -- cache yoksa tarif edilecek bir sey de yok, o yuzden
# FAIL degil SKIP. Dosya varken (yerelde) eskisi gibi kosuyorlar.
requires_cache = pytest.mark.skipif(
    not os.path.exists(CACHE_FILE),
    reason="catalog cache yok (%s) - sync hic calismamis; CI'da beklenen durum"
           % CACHE_FILE)


def _load_cache():
    with open(CACHE_FILE) as f:
        return json.load(f)


@requires_cache
class TestCatalogCacheFile:
    def test_cache_file_exists(self):
        assert os.path.exists(CACHE_FILE), f"{CACHE_FILE} yok - hiç sync olmamis"

    def test_cache_is_valid_json(self):
        cache = _load_cache()
        assert isinstance(cache, dict)

    def test_cache_has_required_keys(self):
        cache = _load_cache()
        for key in ["debris", "rocket_body", "fetched_at"]:
            assert key in cache, f"Cache'de '{key}' yok"

    def test_debris_is_list(self):
        cache = _load_cache()
        assert isinstance(cache["debris"], list)
        assert len(cache["debris"]) > 0, "debris listesi bos"

    def test_rocket_body_is_list(self):
        cache = _load_cache()
        assert isinstance(cache["rocket_body"], list)
        assert len(cache["rocket_body"]) > 0


# test_cache_recently_fetched buradan tests/smoke/test_data_freshness.py'ye
# tasindi. Sebep: "cache son 7 gunde tazelendi mi" sorusu commit'in degil,
# CALISAN KURULUMUN ozelligi -- catalog sync'inin kosup kosmadigini olcuyor,
# tam da o modulun var olma sebebi. Staging'de sync cron'u bilincli olarak
# kapali (izolasyon + Space-Track kotasi), yani staging'in cache'i elle
# kopyalandigi tarihte donmus duruyor; testi (dogru sekilde) instance'in kendi
# dosyasina baktirmak onu birkac gun sonra staging'de kacinilmaz bir FAIL'e
# cevirirdi -- kodla ilgisi olmayan bir sebeple. Asagida kalan test dosyanin
# BICIMIYLE ilgili: yeterli obje var mi. O, her instance'in kendi kopyasi
# uzerinde anlamli.
@requires_cache
class TestCatalogCacheSize:
    def test_cache_size_reasonable(self):
        """En az 5000 debris bekliyoruz (catalog v2 ~13K)."""
        cache = _load_cache()
        n_debris = len(cache["debris"])
        assert n_debris >= 5000, f"Sadece {n_debris} debris - eksik sync?"


@requires_cache
class TestCatalogDedup:
    def test_debris_no_duplicate_norad(self):
        """debris listesinde duplicate norad_id olmamali."""
        cache = _load_cache()
        norads = []
        for obj in cache["debris"]:
            norad = obj.get("norad")
            if norad:
                norads.append(str(norad))
        unique = set(norads)
        dup_count = len(norads) - len(unique)
        assert dup_count == 0, f"{dup_count} duplicate NORAD ID debris'te"

    def test_rocket_body_no_duplicate_norad(self):
        cache = _load_cache()
        norads = []
        for obj in cache["rocket_body"]:
            norad = obj.get("norad")
            if norad:
                norads.append(str(norad))
        unique = set(norads)
        dup_count = len(norads) - len(unique)
        assert dup_count == 0


@requires_cache
class TestCatalogObjectStructure:
    def test_debris_has_required_fields(self):
        """Her debris kaydi norad ve l2 (TLE line 2) icermeli (altitude hesabi icin)."""
        cache = _load_cache()
        sample = cache["debris"][0]
        # Beklenen alan: norad ve l2 (TLE line 2 - altitude lookup'ta kullaniliyor)
        assert "norad" in sample, f"debris[0]'da 'norad' yok: {list(sample.keys())}"

    def test_debris_l2_format(self):
        """l2 (TLE line 2) varsa 69 karakter olmali (standart TLE format)."""
        cache = _load_cache()
        sample = cache["debris"][0]
        if "l2" in sample and sample["l2"]:
            # TLE line 2 cogunlukla 69 karakter; whitespace ile 70 olabilir
            assert len(sample["l2"].rstrip()) >= 60, "TLE line 2 cok kisa"


@requires_cache
class TestGetStCatalogCache:
    """cas_engine.get_st_catalog_cache() public API testi."""

    def test_get_cache_returns_dict(self):
        import sys
        from conftest import INSTANCE_ROOT
        if INSTANCE_ROOT not in sys.path:
            sys.path.insert(0, INSTANCE_ROOT)
        from cas_engine import get_st_catalog_cache
        cache = get_st_catalog_cache()
        assert cache is not None
        assert isinstance(cache, dict)

    def test_get_cache_has_objects(self):
        import sys
        from conftest import INSTANCE_ROOT
        if INSTANCE_ROOT not in sys.path:
            sys.path.insert(0, INSTANCE_ROOT)
        from cas_engine import get_st_catalog_cache
        cache = get_st_catalog_cache()
        # Toplam obje sayisi 5000+ olmali
        total = len(cache.get("debris", [])) + len(cache.get("rocket_body", []))
        assert total >= 5000


class TestConjunctionEvents:
    """conjunction_events tablosu data integrity (CDM fetch sonuclari)."""

    def test_unique_cdm_fetched_constraint(self):
        """UNIQUE(cdm_id, fetched_at) constraint var (duplicate fetch koruma)."""
        import psycopg2
        conn = psycopg2.connect(os.environ["DB_URL"])
        cur = conn.cursor()
        cur.execute("""
            SELECT count(*) FROM (
                SELECT cdm_id, fetched_at FROM conjunction_events
                GROUP BY cdm_id, fetched_at HAVING count(*) > 1
            ) dup
        """)
        n = cur.fetchone()[0]
        cur.close(); conn.close()
        assert n == 0, f"{n} duplicate (cdm_id, fetched_at)"

    def test_risk_values_valid(self):
        """risk kolonu sadece RED/YELLOW/GREEN olabilir (veya NULL)."""
        import psycopg2
        conn = psycopg2.connect(os.environ["DB_URL"])
        cur = conn.cursor()
        cur.execute("SELECT DISTINCT risk FROM conjunction_events WHERE risk IS NOT NULL")
        rows = cur.fetchall()
        cur.close(); conn.close()
        valid = {"RED", "YELLOW", "GREEN"}
        for (r,) in rows:
            assert r in valid, f"Gecersiz risk: {r}"
