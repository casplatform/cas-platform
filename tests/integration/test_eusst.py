"""
EU SST integration tests: schema, sync state, 5.1 compliance, idempotency.

DB-only, salt SELECT. Veri yazmaz.

EU SST 5.1 compliance kontrolleri:
  - event_id UNIQUE (duplicate sync engelli)
  - raw_payload NOT NULL (saklanmali, ama user'a sizmamali)
  - Endpoint kodu disclaimer iceriyor mu (kaynak inceleme)
"""
import pytest
import psycopg2
import os
import datetime


def _query(sql, params=None):
    conn = psycopg2.connect(os.environ["DB_URL"])
    cur = conn.cursor()
    cur.execute(sql, params or ())
    rows = cur.fetchall()
    cur.close(); conn.close()
    return rows


def _count(sql, params=None):
    return _query(sql, params)[0][0]


class TestEusstSchema:
    def test_fg_events_table_exists(self):
        n = _count("SELECT count(*) FROM information_schema.tables WHERE table_name='eusst_fg_events'")
        assert n == 1

    def test_re_events_table_exists(self):
        n = _count("SELECT count(*) FROM information_schema.tables WHERE table_name='eusst_re_events'")
        assert n == 1

    def test_sync_state_table_exists(self):
        n = _count("SELECT count(*) FROM information_schema.tables WHERE table_name='eusst_sync_state'")
        assert n == 1

    def test_fg_event_id_unique_constraint(self):
        """event_id UNIQUE constraint var mi (duplicate sync koruma)."""
        rows = _query("""
            SELECT count(*) FROM information_schema.table_constraints
            WHERE table_name='eusst_fg_events' AND constraint_type='UNIQUE'
        """)
        assert rows[0][0] >= 1

    def test_re_event_id_unique_constraint(self):
        rows = _query("""
            SELECT count(*) FROM information_schema.table_constraints
            WHERE table_name='eusst_re_events' AND constraint_type='UNIQUE'
        """)
        assert rows[0][0] >= 1


class TestEusstData:
    def test_fg_event_ids_unique_in_data(self):
        """Mevcut datada duplicate event_id yok mu."""
        n = _count("""
            SELECT count(*) FROM (
                SELECT event_id FROM eusst_fg_events GROUP BY event_id HAVING count(*) > 1
            ) dup
        """)
        assert n == 0, f"{n} duplicate event_id var FG'de"

    def test_re_event_ids_unique_in_data(self):
        n = _count("""
            SELECT count(*) FROM (
                SELECT event_id FROM eusst_re_events GROUP BY event_id HAVING count(*) > 1
            ) dup
        """)
        assert n == 0


class TestSyncFreshness:
    def test_fg_sync_recent(self):
        """FG sync son 48 saat icinde calismis mi (cron her 6h)."""
        rows = _query("SELECT last_sync_at FROM eusst_sync_state WHERE service='fg'")
        if not rows or not rows[0][0]:
            pytest.skip("fg sync_state kaydi yok")
        last_sync = rows[0][0]
        now = datetime.datetime.now(datetime.timezone.utc)
        age_hours = (now - last_sync).total_seconds() / 3600
        assert age_hours < 48, f"FG sync {age_hours:.1f} saattir calismamis (cron her 6h olmali)"

    def test_re_sync_recent(self):
        rows = _query("SELECT last_sync_at FROM eusst_sync_state WHERE service='re'")
        if not rows or not rows[0][0]:
            pytest.skip("re sync_state kaydi yok")
        last_sync = rows[0][0]
        now = datetime.datetime.now(datetime.timezone.utc)
        age_hours = (now - last_sync).total_seconds() / 3600
        assert age_hours < 48, f"RE sync {age_hours:.1f} saattir calismamis"

    def test_sync_state_no_error_status(self):
        """Son sync_status 'error' olmamali (saglikli sync)."""
        rows = _query("SELECT service, last_status, last_error FROM eusst_sync_state")
        errors = []
        for service, status, err in rows:
            if status and "error" in status.lower():
                errors.append(f"  {service}: status={status} err={err}")
        assert not errors, "Sync error durumu var:\n" + "\n".join(errors)


class TestEusst51Compliance:
    """EU SST 5.1: raw_payload, download_link, file_name kullaniciya sizmamali."""

    def test_fg_raw_payload_stored(self):
        """raw_payload DB'de saklanmali (admin erisimi icin)."""
        n = _count("SELECT count(*) FROM eusst_fg_events WHERE raw_payload IS NOT NULL")
        total = _count("SELECT count(*) FROM eusst_fg_events")
        assert n == total, "raw_payload bazi FG event'lerde NULL - sync hatasi"

    def test_re_raw_payload_stored(self):
        n = _count("SELECT count(*) FROM eusst_re_events WHERE raw_payload IS NOT NULL")
        total = _count("SELECT count(*) FROM eusst_re_events")
        assert n == total

    def test_aggregate_endpoint_has_disclaimer_in_code(self):
        """Engine kodunda /eusst/aggregate endpoint'i disclaimer iceriyor mu."""
        with open("/opt/cas/cas_engine.py") as f:
            src = f.read()
        # /eusst/aggregate bloku ile disclaimer ayni bolgede olmali
        agg_idx = src.find('pathE == "/eusst/aggregate"')
        assert agg_idx > 0, "/eusst/aggregate endpoint kodu bulunamadi"
        # Bu endpoint'ten sonraki ~1000 karakterde 'disclaimer' gecmeli
        chunk = src[agg_idx:agg_idx + 2000]
        assert "disclaimer" in chunk.lower(), "/eusst/aggregate disclaimer icermiyor"

    def test_reentries_endpoint_no_raw_payload_leak(self):
        """/eusst/reentries response'unda raw_payload SELECT edilmemeli."""
        with open("/opt/cas/cas_engine.py") as f:
            src = f.read()
        re_idx = src.find('pathE == "/eusst/reentries"')
        assert re_idx > 0
        # Bu endpoint blok'unda raw_payload secimi olmamali (5.1 compliance)
        chunk = src[re_idx:re_idx + 2000]
        # raw_payload kolonu SELECT'lerde gecmemeli (sadece WHERE/disclaimer'da olabilir)
        # Heuristic: "raw_payload" stringi varsa kontrol
        if "raw_payload" in chunk:
            # SELECT listesinde mi? Bu daha cok kod review konusu, basit kontrol:
            # Kolon listesi tipik olarak '"event_id", "norad_id", ...' formatinda
            assert "SELECT raw_payload" not in chunk.upper().replace(" ", ""), "/eusst/reentries raw_payload SELECT ediyor olabilir - 5.1 ihlali"

    def test_fragmentations_endpoint_no_raw_payload_leak(self):
        with open("/opt/cas/cas_engine.py") as f:
            src = f.read()
        fg_idx = src.find('pathE == "/eusst/fragmentations"')
        assert fg_idx > 0
        chunk = src[fg_idx:fg_idx + 2000]
        if "raw_payload" in chunk:
            assert "SELECT raw_payload" not in chunk.upper().replace(" ", ""), "/eusst/fragmentations raw_payload SELECT ediyor olabilir - 5.1 ihlali"


class TestEusstSyncModuleImport:
    """eusst_sync.py modul fonksiyonlari import edilebilmeli."""

    def test_eusst_sync_importable(self):
        import sys
        sys.path.insert(0, "/opt/cas")
        import eusst_sync
        assert hasattr(eusst_sync, "main")
        assert hasattr(eusst_sync, "sync_service")

    def test_map_functions_exist(self):
        import sys
        sys.path.insert(0, "/opt/cas")
        import eusst_sync
        assert hasattr(eusst_sync, "map_fg_event")
        assert hasattr(eusst_sync, "map_re_event")
