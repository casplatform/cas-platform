"""Reporting service — monthly/annual operator reports (read-only aggregation).

TÜBİTAK UZAY feedback #2: periodic reporting outputs for process management
and retrospective analysis.

Design:
- Pure aggregation over existing tables; no schema changes, no engine touches.
- Scope follows the /history precedent: operator -> watchlist NORADs,
  admin -> global. Empty watchlist -> empty report (fail-closed).
- Synthetic/demo conjunctions (raw_json->>'synthetic' = 'true') are excluded.
- Period basis: TCA for conjunction activity (operator-meaningful),
  operator_action_at for decision activity, fetched_at for space weather.
- Dedup: DISTINCT ON (cdm_id) latest state — consistent with landing-stats
  and the ADR ranker.
"""
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional

from core.database import get_dict_cursor

_LATEST_CTE = """
WITH latest AS (
  SELECT DISTINCT ON (cdm_id) cdm_id, sat1, sat2, norad1, norad2, tca,
         miss_dist_m, pc, risk, raw_json
  FROM conjunction_events
  ORDER BY cdm_id, fetched_at DESC
), scoped AS (
  SELECT * FROM latest
  WHERE tca >= %(start)s AND tca < %(end)s
    AND COALESCE(raw_json->>'synthetic','') <> 'true'
    AND COALESCE(raw_json->>'source','') <> 'CAS real-geometry screening'
    {scope_clause}
)
"""

_SCOPE_SQL = "AND (norad1 = ANY(%(norads)s) OR norad2 = ANY(%(norads)s))"


def _f(x) -> Optional[float]:
    return float(x) if x is not None else None


def _iso(x) -> Optional[str]:
    return x.isoformat() if x is not None else None


def _month_bounds(year: int, month: int):
    start = date(year, month, 1)
    end = date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)
    return start, end


def _watchlist(user_id: int) -> List[Dict[str, str]]:
    with get_dict_cursor() as cur:
        cur.execute(
            "SELECT norad_id, sat_name FROM watchlist WHERE user_id=%s ORDER BY sat_name",
            (user_id,),
        )
        return [
            {"norad_id": str(r["norad_id"]).strip(), "sat_name": r["sat_name"]}
            for r in cur.fetchall() if r.get("norad_id")
        ]


def _observation_window() -> Dict[str, Any]:
    try:
        with get_dict_cursor() as cur:
            cur.execute(
                "SELECT first_observation, last_observation, days_observing, "
                "unique_cdm_count FROM cas_observation_window"
            )
            r = cur.fetchone()
            if not r:
                return {}
            return {
                "first_observation": _iso(r["first_observation"]),
                "last_observation": _iso(r["last_observation"]),
                "days_observing": int(r["days_observing"] or 0),
                "unique_cdm_count": int(r["unique_cdm_count"] or 0),
            }
    except Exception:
        return {}


def _conjunction_block(params: Dict[str, Any], scope_clause: str) -> Dict[str, Any]:
    """Summary + risk breakdown + update volume for the period."""
    out: Dict[str, Any] = {}
    with get_dict_cursor() as cur:
        cur.execute(
            _LATEST_CTE.format(scope_clause=scope_clause)
            + """
            SELECT COUNT(*) AS unique_conjunctions,
                   MAX(pc) AS max_pc,
                   MIN(miss_dist_m) AS min_miss_m,
                   percentile_cont(0.5) WITHIN GROUP (ORDER BY miss_dist_m) AS median_miss_m
            FROM scoped
            """,
            params,
        )
        r = cur.fetchone()
        out["unique_conjunctions"] = int(r["unique_conjunctions"] or 0)
        out["max_pc"] = _f(r["max_pc"])
        out["min_miss_m"] = _f(r["min_miss_m"])
        out["median_miss_m"] = _f(r["median_miss_m"])

        cur.execute(
            _LATEST_CTE.format(scope_clause=scope_clause)
            + "SELECT COALESCE(risk,'UNKNOWN') AS risk, COUNT(*) AS n FROM scoped GROUP BY 1",
            params,
        )
        out["by_risk"] = {r2["risk"]: int(r2["n"]) for r2 in cur.fetchall()}

        # CDM update volume (all rows, not deduped) for the same period/scope
        upd_sql = (
            "SELECT COUNT(*) AS n FROM conjunction_events "
            "WHERE tca >= %(start)s AND tca < %(end)s "
            "AND COALESCE(raw_json->>'synthetic','') <> 'true' "
            "AND COALESCE(raw_json->>'source','') <> 'CAS real-geometry screening' "
        )
        if scope_clause:
            upd_sql += _SCOPE_SQL
        cur.execute(upd_sql, params)
        out["cdm_updates"] = int(cur.fetchone()["n"] or 0)
    return out


def _satellite_breakdown(params: Dict[str, Any], wl: List[Dict[str, str]]) -> List[Dict[str, Any]]:
    if not wl:
        return []
    name_by_norad = {w["norad_id"]: w["sat_name"] for w in wl}
    with get_dict_cursor() as cur:
        cur.execute(
            _LATEST_CTE.format(scope_clause=_SCOPE_SQL)
            + """
            SELECT w.norad AS norad_id,
                   COUNT(*) AS unique_conjunctions,
                   MAX(s.pc) AS max_pc,
                   MIN(s.miss_dist_m) AS min_miss_m,
                   MAX(s.tca) AS last_tca
            FROM scoped s
            JOIN unnest(%(norads)s::text[]) AS w(norad)
              ON s.norad1 = w.norad OR s.norad2 = w.norad
            GROUP BY w.norad
            ORDER BY COUNT(*) DESC
            """,
            params,
        )
        rows = cur.fetchall()
    return [
        {
            "norad_id": r["norad_id"],
            "sat_name": name_by_norad.get(r["norad_id"], ""),
            "unique_conjunctions": int(r["unique_conjunctions"]),
            "max_pc": _f(r["max_pc"]),
            "min_miss_m": _f(r["min_miss_m"]),
            "last_tca": _iso(r["last_tca"]),
        }
        for r in rows
    ]


def _top_counterparties(params: Dict[str, Any]) -> List[Dict[str, Any]]:
    """For operators: the objects most often on the other side."""
    with get_dict_cursor() as cur:
        cur.execute(
            _LATEST_CTE.format(scope_clause=_SCOPE_SQL)
            + """
            SELECT other_norad, MAX(other_name) AS name,
                   COUNT(*) AS unique_conjunctions, MAX(pc) AS max_pc
            FROM (
              SELECT CASE WHEN s.norad1 = ANY(%(norads)s) THEN s.norad2 ELSE s.norad1 END AS other_norad,
                     CASE WHEN s.norad1 = ANY(%(norads)s) THEN s.sat2 ELSE s.sat1 END AS other_name,
                     s.pc
              FROM scoped s
            ) t
            WHERE other_norad IS NOT NULL AND other_norad <> '?'
            GROUP BY other_norad
            ORDER BY 3 DESC
            LIMIT 10
            """,
            params,
        )
        rows = cur.fetchall()
    return [
        {
            "norad_id": r["other_norad"],
            "name": (r["name"] or "").strip(),
            "unique_conjunctions": int(r["unique_conjunctions"]),
            "max_pc": _f(r["max_pc"]),
        }
        for r in rows
    ]


def _top_objects_global(params: Dict[str, Any]) -> List[Dict[str, Any]]:
    """For admin: most conjunction-active objects globally in the period."""
    with get_dict_cursor() as cur:
        cur.execute(
            _LATEST_CTE.format(scope_clause="")
            + """
            SELECT norad, MAX(name) AS name,
                   COUNT(*) AS unique_conjunctions, MAX(pc) AS max_pc
            FROM (
              SELECT norad1 AS norad, sat1 AS name, pc FROM scoped
              UNION ALL
              SELECT norad2, sat2, pc FROM scoped
            ) t
            WHERE norad IS NOT NULL AND norad <> '?'
            GROUP BY norad
            ORDER BY 3 DESC
            LIMIT 10
            """,
            params,
        )
        rows = cur.fetchall()
    return [
        {
            "norad_id": r["norad"],
            "name": (r["name"] or "").strip(),
            "unique_conjunctions": int(r["unique_conjunctions"]),
            "max_pc": _f(r["max_pc"]),
        }
        for r in rows
    ]


def _decision_block(start, end, user_id: Optional[int]) -> Dict[str, Any]:
    """Recorded operator actions in the period (+ indicative response time)."""
    p: Dict[str, Any] = {"start": start, "end": end, "uid": user_id}
    uid_clause = "AND user_id = %(uid)s" if user_id is not None else ""
    out: Dict[str, Any] = {"actions_total": 0, "by_action": {}, "median_response_hours": None}
    with get_dict_cursor() as cur:
        cur.execute(
            f"""
            SELECT COALESCE(operator_action,'(none)') AS action, COUNT(*) AS n
            FROM decision_results
            WHERE operator_action_at >= %(start)s AND operator_action_at < %(end)s
              {uid_clause}
            GROUP BY 1 ORDER BY 2 DESC
            """,
            p,
        )
        rows = cur.fetchall()
        out["by_action"] = {r["action"]: int(r["n"]) for r in rows}
        out["actions_total"] = sum(out["by_action"].values())

        cur.execute(
            f"""
            SELECT percentile_cont(0.5) WITHIN GROUP (
                     ORDER BY EXTRACT(EPOCH FROM (operator_action_at - computed_at))/3600.0
                   ) AS med_h,
                   COUNT(*) AS n
            FROM decision_results
            WHERE operator_action_at >= %(start)s AND operator_action_at < %(end)s
              AND computed_at IS NOT NULL AND operator_action_at >= computed_at
              {uid_clause}
            """,
            p,
        )
        r = cur.fetchone()
        out["median_response_hours"] = _f(r["med_h"])
        out["response_sample_n"] = int(r["n"] or 0)
    out["note"] = ("Response time is measured from the last analysis compute to the "
                   "recorded action; indicative, not a full decision-cycle audit.")
    return out


def _space_weather_block(start, end) -> Dict[str, Any]:
    with get_dict_cursor() as cur:
        cur.execute(
            """
            SELECT COUNT(*) AS n,
                   AVG(kp_index) AS kp_avg,
                   MAX(kp_index) AS kp_max,
                   MAX(f107_flux) AS f107_max,
                   COUNT(DISTINCT CASE WHEN kp_index >= 5 THEN fetched_at::date END) AS elevated_days
            FROM space_weather_snapshots
            WHERE fetched_at >= %(start)s AND fetched_at < %(end)s
            """,
            {"start": start, "end": end},
        )
        r = cur.fetchone()
    return {
        "snapshots": int(r["n"] or 0),
        "kp_avg": _f(r["kp_avg"]),
        "kp_max": _f(r["kp_max"]),
        "f107_max": _f(r["f107_max"]),
        "elevated_days_kp_ge5": int(r["elevated_days"] or 0),
    }


_HONESTY_NOTES = [
    "Conjunction pipeline is RED-focused: the CDM fetcher ingests events with Pc >= 1e-4, "
    "so this report reflects tracked high-risk activity, not all catalogued conjunctions.",
    "CAS is a public-catalogue analysis layer; figures reflect data published by external sources.",
    "Synthetic/demo events and CAS-generated screening scenarios are excluded from all counts.",
    "Decision-support documentation — the operator retains maneuver authority.",
]


def _build(start, end, label: str, report_type: str,
           user_id: int, is_admin: bool) -> Dict[str, Any]:
    scope_mode = "global" if is_admin else "watchlist"
    wl = [] if is_admin else _watchlist(user_id)
    norads = [w["norad_id"] for w in wl]

    params = {"start": start, "end": end, "norads": norads}
    scope_clause = "" if is_admin else _SCOPE_SQL

    today = datetime.now(timezone.utc).date()
    notes = list(_HONESTY_NOTES)
    if end > today:
        notes.insert(0, f"Period incomplete: data available through {min(today, end).isoformat()}.")
    if not is_admin and not norads:
        notes.insert(0, "Watchlist is empty — this report is scoped to your watchlist and is therefore empty.")

    report: Dict[str, Any] = {
        "report_type": report_type,
        "period": {"label": label, "start": start.isoformat(), "end": end.isoformat()},
        "scope": {"mode": scope_mode, "satellites": len(norads)},
        "provenance": {
            "observation_window": _observation_window(),
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "synthetic_excluded": True,
            "period_basis": "TCA (conjunctions), action time (decisions), fetch time (space weather)",
        },
        "summary": _conjunction_block(params, scope_clause),
        "decisions": _decision_block(start, end, None if is_admin else user_id),
        "space_weather": _space_weather_block(start, end),
        "notes": notes,
    }
    if is_admin:
        report["top_objects"] = _top_objects_global(params)
    else:
        report["satellites"] = _satellite_breakdown(params, wl)
        report["top_counterparties"] = _top_counterparties(params) if norads else []
    return report


def monthly_report(year: int, month: int, *, user_id: int, is_admin: bool) -> Dict[str, Any]:
    start, end = _month_bounds(year, month)
    return _build(start, end, f"{year}-{month:02d}", "monthly", user_id, is_admin)


def annual_report(year: int, *, user_id: int, is_admin: bool) -> Dict[str, Any]:
    start, end = date(year, 1, 1), date(year + 1, 1, 1)
    report = _build(start, end, str(year), "annual", user_id, is_admin)

    # Month-by-month trend (same dedup + scope rules)
    scope_clause = "" if is_admin else _SCOPE_SQL
    norads = [s["norad_id"] for s in report.get("satellites", [])] if not is_admin else []
    if not is_admin:
        # satellites list may be sorted/filtered; re-pull full watchlist for scope
        norads = [w["norad_id"] for w in _watchlist(user_id)]
    params = {"start": start, "end": end, "norads": norads}

    trend: Dict[int, Dict[str, Any]] = {
        m: {"month": m, "unique_conjunctions": 0, "max_pc": None, "min_miss_m": None, "actions": 0}
        for m in range(1, 13)
    }
    with get_dict_cursor() as cur:
        cur.execute(
            _LATEST_CTE.format(scope_clause=scope_clause)
            + """
            SELECT EXTRACT(MONTH FROM tca)::int AS m,
                   COUNT(*) AS uniq, MAX(pc) AS max_pc, MIN(miss_dist_m) AS min_miss
            FROM scoped GROUP BY 1
            """,
            params,
        )
        for r in cur.fetchall():
            t = trend[int(r["m"])]
            t["unique_conjunctions"] = int(r["uniq"])
            t["max_pc"] = _f(r["max_pc"])
            t["min_miss_m"] = _f(r["min_miss"])

        uid_clause = "" if is_admin else "AND user_id = %(uid)s"
        cur.execute(
            f"""
            SELECT EXTRACT(MONTH FROM operator_action_at)::int AS m, COUNT(*) AS n
            FROM decision_results
            WHERE operator_action_at >= %(start)s AND operator_action_at < %(end)s
              {uid_clause}
            GROUP BY 1
            """,
            {**params, "uid": user_id},
        )
        for r in cur.fetchall():
            trend[int(r["m"])]["actions"] = int(r["n"])

    report["monthly_trend"] = [trend[m] for m in range(1, 13)]
    return report
