"""Threshold monitoring for insured orbits — INSURER PRO and above.

An underwriter does not watch a screen; they need to be told when an orbit they
carry has materially changed. Three independent triggers, each with a threshold
the user sets:

  threat_growth  — non-manoeuvrable population in the shell grew by X%
  percentile     — the orbit climbed X points in the LEO ranking
  fragmentation  — an EU SST fragmentation event occurred inside the band

After a trigger fires the baseline is re-frozen, so one deterioration produces
one notification rather than a weekly repeat.
"""
import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from core.database import get_dict_cursor
from services import orbital_risk as orisk

log = logging.getLogger(__name__)

TRIGGERS = ("threat_growth", "percentile", "fragmentation")


# ─────────────────────────── CRUD ───────────────────────────
def create(user_id: int, *, label: str, altitude_km: float,
           inclination_deg: Optional[float] = None,
           band_half_km: float = 25.0,
           watch_threat: bool = True, threat_pct: float = 10.0,
           watch_pctl: bool = True, pctl_points: float = 5.0,
           watch_frag: bool = True) -> Dict[str, Any]:
    """Register a watch, freezing today's figures as the baseline."""
    a = orisk.assess_orbit(altitude_km, inclination_deg, band_half_km)
    base_threat = a["catalogue"]["threat"]
    base_pctl = (a.get("percentile") or {}).get("percentile")

    with get_dict_cursor() as cur:
        cur.execute("""
            INSERT INTO insurance_watch
              (user_id,label,altitude_km,inclination_deg,band_half_km,
               watch_threat,threat_pct,watch_pctl,pctl_points,watch_frag,
               baseline_threat,baseline_pctl)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            RETURNING id, created_at
        """, (user_id, label, altitude_km, inclination_deg, band_half_km,
              watch_threat, threat_pct, watch_pctl, pctl_points, watch_frag,
              base_threat, base_pctl))
        r = cur.fetchone()
    return {"id": r["id"], "label": label,
            "baseline_threat": base_threat, "baseline_percentile": base_pctl,
            "created_at": r["created_at"].isoformat() if r["created_at"] else None}


def listing(user_id: int, include_inactive: bool = False) -> List[Dict[str, Any]]:
    q = ["SELECT * FROM insurance_watch WHERE user_id=%s"]
    p: List[Any] = [user_id]
    if not include_inactive:
        q.append("AND is_active = true")
    q.append("ORDER BY created_at DESC")
    with get_dict_cursor() as cur:
        cur.execute(" ".join(q), p)
        out = []
        for r in cur.fetchall():
            d = dict(r)
            for k in ("baseline_at", "last_checked", "last_triggered", "created_at"):
                if d.get(k):
                    d[k] = d[k].isoformat()
            out.append(d)
        return out


def count_active(user_id: int) -> int:
    with get_dict_cursor() as cur:
        cur.execute("SELECT count(*) n FROM insurance_watch "
                    "WHERE user_id=%s AND is_active=true", (user_id,))
        return cur.fetchone()["n"]


def update(user_id: int, watch_id: int, **fields) -> bool:
    allowed = {"label", "threat_pct", "pctl_points", "watch_threat",
               "watch_pctl", "watch_frag", "is_active"}
    sets, vals = [], []
    for k, v in fields.items():
        if k in allowed and v is not None:
            sets.append(f"{k}=%s"); vals.append(v)
    if not sets:
        return False
    vals += [user_id, watch_id]
    with get_dict_cursor() as cur:
        cur.execute(f"UPDATE insurance_watch SET {', '.join(sets)} "
                    "WHERE user_id=%s AND id=%s", vals)
        return cur.rowcount > 0


def remove(user_id: int, watch_id: int) -> bool:
    with get_dict_cursor() as cur:
        cur.execute("DELETE FROM insurance_watch WHERE user_id=%s AND id=%s",
                    (user_id, watch_id))
        return cur.rowcount > 0


def events(user_id: int, limit: int = 50) -> List[Dict[str, Any]]:
    with get_dict_cursor() as cur:
        cur.execute("""SELECT e.*, w.label, w.altitude_km, w.inclination_deg
                       FROM insurance_watch_events e
                       LEFT JOIN insurance_watch w ON w.id = e.watch_id
                       WHERE e.user_id=%s ORDER BY e.created_at DESC LIMIT %s""",
                    (user_id, min(max(limit, 1), 200)))
        out = []
        for r in cur.fetchall():
            d = dict(r)
            if d.get("created_at"):
                d["created_at"] = d["created_at"].isoformat()
            out.append(d)
        return out


# ─────────────────────── trigger evaluation ───────────────────────
def _fragmentation_in_band(alt_km: float, half_km: float,
                           since: Optional[datetime]) -> List[Dict[str, Any]]:
    """EU SST fragmentations whose parent orbit crosses the watched shell."""
    lo, hi = alt_km - half_km, alt_km + half_km
    q = """SELECT event_id, event_epoch, parent1_object_name, parent1_norad_id,
                  parent1_apogee_km, parent1_perigee_km
           FROM eusst_fg_events
           WHERE parent1_perigee_km IS NOT NULL
             AND parent1_apogee_km IS NOT NULL
             AND parent1_perigee_km <= %s AND parent1_apogee_km >= %s"""
    p: List[Any] = [hi, lo]
    if since:
        q += " AND COALESCE(event_epoch, publish_date, creation_date) > %s"
        p.append(since)
    q += " ORDER BY COALESCE(event_epoch, publish_date, creation_date) DESC LIMIT 10"
    try:
        with get_dict_cursor() as cur:
            cur.execute(q, p)
            out = []
            for r in cur.fetchall():
                d = dict(r)
                if d.get("event_epoch"):
                    d["event_epoch"] = d["event_epoch"].isoformat()
                out.append(d)
            return out
    except Exception as e:
        log.warning("insurance_watch: fragmentation query failed: %s", e)
        return []


def _record(cur, w: Dict[str, Any], ttype: str, old, new, delta,
            detail: Dict[str, Any]) -> None:
    cur.execute("""INSERT INTO insurance_watch_events
                     (watch_id,user_id,trigger_type,old_value,new_value,delta,detail)
                   VALUES (%s,%s,%s,%s,%s,%s,%s)""",
                (w["id"], w["user_id"], ttype, old, new, delta, json.dumps(detail)))


def check_one(w: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Evaluate a single watch. Returns the triggers that fired."""
    fired: List[Dict[str, Any]] = []
    try:
        a = orisk.assess_orbit(float(w["altitude_km"]),
                               float(w["inclination_deg"]) if w["inclination_deg"] is not None else None,
                               float(w["band_half_km"] or 25.0))
    except Exception as e:
        log.warning("insurance_watch: assess failed for watch %s: %s", w["id"], e)
        return fired

    cur_threat = a["catalogue"]["threat"]
    cur_pctl = (a.get("percentile") or {}).get("percentile")
    now = datetime.now(timezone.utc)

    with get_dict_cursor() as cur:
        # 1) threat population growth
        if w.get("watch_threat") and w.get("baseline_threat"):
            base = float(w["baseline_threat"])
            if base > 0:
                growth = (cur_threat - base) / base * 100.0
                if growth >= float(w["threat_pct"] or 10.0):
                    _record(cur, w, "threat_growth", base, cur_threat, round(growth, 1),
                            {"threshold_pct": w["threat_pct"],
                             "band_km": [w["altitude_km"] - w["band_half_km"],
                                         w["altitude_km"] + w["band_half_km"]]})
                    fired.append({"type": "threat_growth", "old": base,
                                  "new": cur_threat, "delta_pct": round(growth, 1)})

        # 2) percentile climb
        if w.get("watch_pctl") and w.get("baseline_pctl") is not None and cur_pctl is not None:
            climb = cur_pctl - int(w["baseline_pctl"])
            if climb >= float(w["pctl_points"] or 5.0):
                _record(cur, w, "percentile", w["baseline_pctl"], cur_pctl, climb,
                        {"threshold_points": w["pctl_points"]})
                fired.append({"type": "percentile", "old": w["baseline_pctl"],
                              "new": cur_pctl, "delta_points": climb})

        # 3) fragmentation inside the band
        if w.get("watch_frag"):
            since = w.get("last_checked") or w.get("baseline_at")
            frags = _fragmentation_in_band(float(w["altitude_km"]),
                                           float(w["band_half_km"] or 25.0), since)
            if frags:
                _record(cur, w, "fragmentation", None, len(frags), len(frags),
                        {"events": frags[:5]})
                fired.append({"type": "fragmentation", "count": len(frags),
                              "events": frags[:3]})

        # refresh state — re-freeze the baseline so one change = one alert
        if fired:
            cur.execute("""UPDATE insurance_watch
                           SET last_checked=%s, last_triggered=%s,
                               trigger_count=trigger_count+1,
                               baseline_threat=%s, baseline_pctl=%s, baseline_at=%s
                           WHERE id=%s""",
                        (now, now, cur_threat, cur_pctl, now, w["id"]))
        else:
            cur.execute("UPDATE insurance_watch SET last_checked=%s WHERE id=%s",
                        (now, w["id"]))
    return fired


def check_all() -> Dict[str, Any]:
    """Sweep every active watch. Intended for a weekly cron."""
    with get_dict_cursor() as cur:
        cur.execute("SELECT * FROM insurance_watch WHERE is_active=true "
                    "ORDER BY last_checked NULLS FIRST")
        watches = [dict(r) for r in cur.fetchall()]

    total_fired = 0
    per_user: Dict[int, List[Dict[str, Any]]] = {}
    for w in watches:
        fired = check_one(w)
        if fired:
            total_fired += len(fired)
            per_user.setdefault(w["user_id"], []).append(
                {"watch": w["label"],
                 "altitude_km": w["altitude_km"],
                 "inclination_deg": w["inclination_deg"],
                 "triggers": fired})
    return {"checked": len(watches), "fired": total_fired,
            "users_to_notify": per_user,
            "at": datetime.now(timezone.utc).isoformat()}
