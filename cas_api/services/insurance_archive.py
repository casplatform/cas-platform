"""Immutable report archive for insurance assessments.

A report is frozen at generation time: the full assessment payload is stored as
JSONB and never recomputed. Re-downloading report #A4F2 six months later yields
exactly the figures the underwriter saw when the decision was made — which is
what makes it defensible in an audit or a dispute.
"""
import hashlib
import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from core.database import get_dict_cursor

log = logging.getLogger(__name__)


def make_report_id(user_id: int, assessment: Dict[str, Any]) -> str:
    """Short, human-quotable id. Includes a timestamp so re-running the same
    orbit later produces a *new* report rather than silently overwriting."""
    o = assessment.get("orbit", {})
    seed = (f"{user_id}|{o.get('altitude_km')}|{o.get('inclination_deg')}|"
            f"{datetime.now(timezone.utc).isoformat()}")
    return "#" + hashlib.sha256(seed.encode()).hexdigest()[:4].upper()


def _subject(assessment: Dict[str, Any]) -> str:
    o = assessment.get("orbit", {})
    inc = o.get("inclination_deg")
    return (f"{o.get('altitude_km')} km / "
            f"{inc:.1f}\u00b0" if inc is not None
            else f"{o.get('altitude_km')} km / all inclinations")


def save(user_id: int, assessment: Dict[str, Any], *,
         org: Optional[str] = None, is_demo: bool = False,
         report_id: Optional[str] = None) -> str:
    """Freeze an assessment into the archive. Returns the report id."""
    rid = report_id or make_report_id(user_id, assessment)
    o = assessment.get("orbit", {})
    c = assessment.get("catalogue", {})
    b = assessment.get("burden", {})
    cas = assessment.get("cascade", {})
    tr = (assessment.get("trend") or {}).get("primary") or {}

    try:
        with get_dict_cursor() as cur:
            cur.execute("""
                INSERT INTO insurance_reports
                  (report_id, user_id, org, subject, altitude_km, inclination_deg,
                   mode, lambda_per_year, threat_objects, trend_pct, cascade_years,
                   assessment, catalogue_epoch, is_demo)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (report_id, user_id) DO NOTHING
            """, (
                rid, user_id, org, _subject(assessment),
                o.get("altitude_km"), o.get("inclination_deg"),
                "inclination-gated" if c.get("inclination_gated") else "band-based",
                b.get("lambda_per_year"), c.get("threat"),
                tr.get("cagr_pct_per_year") if tr.get("available") else None,
                cas.get("cloud_clearing_years_90pct"),
                json.dumps(assessment), datetime.now(timezone.utc), is_demo,
            ))
    except Exception as e:
        log.warning("insurance_archive: save failed: %s", e)
    return rid


def get(user_id: int, report_id: str) -> Optional[Dict[str, Any]]:
    """Retrieve the frozen assessment. No recomputation."""
    rid = report_id if report_id.startswith("#") else "#" + report_id
    try:
        with get_dict_cursor() as cur:
            cur.execute("""SELECT report_id, org, subject, assessment, is_demo,
                                  created_at, catalogue_epoch
                           FROM insurance_reports
                           WHERE user_id=%s AND upper(report_id)=upper(%s)""",
                        (user_id, rid))
            r = cur.fetchone()
            if not r:
                return None
            a = r["assessment"]
            if isinstance(a, str):
                a = json.loads(a)
            return {"report_id": r["report_id"], "org": r["org"],
                    "subject": r["subject"], "assessment": a,
                    "is_demo": r["is_demo"],
                    "created_at": r["created_at"].isoformat() if r["created_at"] else None,
                    "catalogue_epoch": r["catalogue_epoch"].isoformat() if r["catalogue_epoch"] else None}
    except Exception as e:
        log.warning("insurance_archive: get failed: %s", e)
        return None


def search(user_id: int, *, q: Optional[str] = None,
           date_from: Optional[str] = None, date_to: Optional[str] = None,
           limit: int = 100) -> List[Dict[str, Any]]:
    """List archived reports; `q` matches the report id (exact retrieval)."""
    sql = ["SELECT report_id, subject, altitude_km, inclination_deg, mode,",
           "       lambda_per_year, threat_objects, trend_pct, cascade_years,",
           "       is_demo, created_at, org",
           "FROM insurance_reports WHERE user_id=%s"]
    params: List[Any] = [user_id]
    if q:
        needle = q.strip().lstrip("#").upper()
        sql.append("AND upper(report_id) LIKE %s")
        params.append(f"%{needle}%")
    if date_from:
        sql.append("AND created_at >= %s"); params.append(date_from)
    if date_to:
        sql.append("AND created_at <= %s"); params.append(date_to)
    sql.append("ORDER BY created_at DESC LIMIT %s")
    params.append(min(max(limit, 1), 500))
    try:
        with get_dict_cursor() as cur:
            cur.execute(" ".join(sql), params)
            out = []
            for r in cur.fetchall():
                d = dict(r)
                if d.get("created_at"):
                    d["created_at"] = d["created_at"].isoformat()
                out.append(d)
            return out
    except Exception as e:
        log.warning("insurance_archive: search failed: %s", e)
        return []


def count(user_id: int) -> int:
    try:
        with get_dict_cursor() as cur:
            cur.execute("SELECT count(*) n FROM insurance_reports WHERE user_id=%s",
                        (user_id,))
            return cur.fetchone()["n"]
    except Exception:
        return 0
