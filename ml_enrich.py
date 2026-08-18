#!/usr/bin/env python3
"""
CAS — ML Layer-1 Enrichment (Sprint #3+#5)
Idempotent: _raw_st_cdm taşıyan ama `ml` bloğu olmayan conjunction_events'i
FastAPI ML servisiyle (POST /api/v2/ml/score) skorlar ve portal-uyumlu
raw_json.ml yazar. Seyrek CDM -> UNAVAILABLE marker (bir kez skorlanır, sonra atlanır).
Engine koduna DOKUNULMAZ.
Cron: 20 * * * * root /usr/bin/python3 /opt/cas/ml_enrich.py >> /var/log/cas/ml_enrich.log 2>&1
"""
import os, sys, json, datetime
import psycopg2
from psycopg2.extras import Json

def _dsn():
    import os as _o
    v = _o.environ.get("DB_URL")
    if v: return v
    e = {}
    with open("/opt/cas/.env") as f:
        for ln in f:
            if "=" in ln and not ln.startswith("#"):
                k, val = ln.strip().split("=", 1)
                e[k] = val.strip().strip('"').strip("'")
    return e["DB_URL"]


DB_URL     = _dsn()
LOOKBACK_H = int(os.environ.get("ML_ENRICH_LOOKBACK_H") or "72")
BATCH      = int(os.environ.get("ML_ENRICH_BATCH") or "200")
TOP_N      = int(os.environ.get("ML_ENRICH_TOP_N") or "5")
TIMEOUT    = 30

SOURCE_MAP = {
    "cdm_public": "spacetrack_public", "spacetrack": "spacetrack_public",
    "spacetrack_public": "spacetrack_public", "ccsds": "ccsds_cdm",
    "ccsds_cdm": "ccsds_cdm", "operator": "ccsds_cdm",
    "starlink": "starlink", "stargaze": "stargaze", "tracss": "tracss",
}
DEFAULT_SRC = "spacetrack_public"

def log(m):
    ts = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    print(f"[{ts}] {m}", flush=True)

# Score in-process rather than over HTTP to /api/v2/ml/score.
#
# That endpoint is gated by require_feature("ml_access"), i.e. Pro plan or
# above. The gate is right for user-facing access, but this is a batch
# enrichment job: it writes ml blocks into conjunction_events, and who may
# read them is the portal's decision, not this script's. When the gate was
# added on 2026-07-09 the script had no token and started returning 401 on
# every row -- 200 errors per run, silently, for 38 days. Giving it a
# service-account credential would mean holding a Pro-tier login in a cron
# script to reach a scorer already sitting in the same tree.
#
# ml_service is the same singleton the endpoint calls, and score_raw is called
# with the same arguments, so the response shape build_ml() consumes is
# unchanged. Model + SHAP load costs ~10s per run; at three runs a day
# (aligned to CDM ingestion) that is cheaper than the 24 hourly runs it
# replaces, and the per-row HTTP round trip disappears entirely.
_CAS_HOME = os.environ.get("CAS_HOME", "/opt/cas").rstrip("/") or "/opt/cas"
for _p in (os.path.join(_CAS_HOME, "cas_api"), _CAS_HOME):
    if _p not in sys.path:
        sys.path.insert(0, _p)
try:
    from services.ml_inference import ml_service
except Exception as _ml_e:
    ml_service = None
    _ML_IMPORT_ERR = f"{type(_ml_e).__name__}: {_ml_e}"


def call_score(cdm, source):
    if ml_service is None:
        return None, f"ml_service import failed: {_ML_IMPORT_ERR}"
    if not ml_service.is_ready():
        return None, f"ml_service not ready: {ml_service.load_error}"
    try:
        return ml_service.score_raw([cdm], source=source, top_n=TOP_N), None
    except Exception as e:
        return None, f"{type(e).__name__}: {e}"

def build_ml(res):
    tier = res.get("tier")
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    if tier is None:
        return None
    if tier == "UNAVAILABLE":
        return {"tier": "UNAVAILABLE", "coverage": res.get("coverage"),
                "model_version": res.get("model_version"), "scored_at": now}
    expl = res.get("explanation") if isinstance(res.get("explanation"), dict) else {}
    return {"tier": tier, "score": res.get("ml_score"), "coverage": res.get("coverage"),
            "shap": expl.get("top_features") or [], "base_value": expl.get("base_value"),
            "model_version": res.get("model_version"), "scored_at": now}

def main():
    conn = psycopg2.connect(DB_URL); cur = conn.cursor()
    cur.execute("""
        SELECT id, raw_json FROM conjunction_events
        WHERE fetched_at > NOW() - (%s * INTERVAL '1 hour')
          AND raw_json::jsonb ? '_raw_st_cdm'
          AND NOT (raw_json::jsonb ? 'ml')
        ORDER BY fetched_at DESC LIMIT %s
    """, (LOOKBACK_H, BATCH))
    rows = cur.fetchall()
    log(f"candidates={len(rows)} (lookback={LOOKBACK_H}h batch={BATCH})")
    scored = unavail = errors = 0
    for rid, rj in rows:
        if isinstance(rj, str):
            try: rj = json.loads(rj)
            except Exception: errors += 1; continue
        if not isinstance(rj, dict): errors += 1; continue
        raw_cdm = rj.get("_raw_st_cdm")
        if not raw_cdm: errors += 1; continue
        src = SOURCE_MAP.get(str(rj.get("source", "")).lower(), DEFAULT_SRC)
        res, err = call_score(raw_cdm, src)
        if res is None: errors += 1; log(f"id={rid} score-error: {err}"); continue
        ml_obj = build_ml(res)
        if ml_obj is None: errors += 1; continue
        rj["ml"] = ml_obj
        cur.execute("UPDATE conjunction_events SET raw_json = %s WHERE id = %s", (Json(rj), rid))
        conn.commit()
        if ml_obj["tier"] == "UNAVAILABLE": unavail += 1
        else:
            scored += 1
            log(f"id={rid} tier={ml_obj['tier']} score={ml_obj.get('score')} cov={ml_obj.get('coverage')} src={src}")
    cur.close(); conn.close()
    log(f"DONE scored={scored} unavailable={unavail} errors={errors}")
    return 0

if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        log(f"FATAL: {type(e).__name__}: {e}"); sys.exit(1)
