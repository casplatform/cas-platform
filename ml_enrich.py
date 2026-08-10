#!/usr/bin/env python3
"""
CAS — ML Layer-1 Enrichment (Sprint #3+#5)
Idempotent: _raw_st_cdm taşıyan ama `ml` bloğu olmayan conjunction_events'i
FastAPI ML servisiyle (POST /api/v2/ml/score) skorlar ve portal-uyumlu
raw_json.ml yazar. Seyrek CDM -> UNAVAILABLE marker (bir kez skorlanır, sonra atlanır).
Engine koduna DOKUNULMAZ.
Cron: 20 * * * * root /usr/bin/python3 /opt/cas/ml_enrich.py >> /var/log/cas/ml_enrich.log 2>&1
"""
import os, sys, json, datetime, http.client
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
ML_HOST    = os.environ.get("ML_HOST", "127.0.0.1")
ML_PORT    = int(os.environ.get("ML_PORT", "8766"))
SCORE_PATH = "/api/v2/ml/score"
LOOKBACK_H = int(os.environ.get("ML_ENRICH_LOOKBACK_H", "72"))
BATCH      = int(os.environ.get("ML_ENRICH_BATCH", "200"))
TOP_N      = int(os.environ.get("ML_ENRICH_TOP_N", "5"))
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

def call_score(cdm, source):
    body = json.dumps({"cdm": cdm, "source": source, "top_n_features": TOP_N}).encode("utf-8")
    conn = http.client.HTTPConnection(ML_HOST, ML_PORT, timeout=TIMEOUT)
    try:
        conn.request("POST", SCORE_PATH, body=body,
                     headers={"Content-Type": "application/json", "Content-Length": str(len(body))})
        r = conn.getresponse(); raw = r.read().decode("utf-8")
        if r.status != 200:
            return None, f"HTTP {r.status}: {raw[:160]}"
        return json.loads(raw), None
    finally:
        conn.close()

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
