#!/usr/bin/env python3
"""
CAS Decision Scanner — Standalone
Runs outside the engine process, doesn't block API.
Triggered by cron or manually.
Reads watchlist → fetches conjunctions → generates decisions → writes to DB.
"""
import psycopg2, os, json, time, sys, math, datetime

# Load env FIRST. cas_engine builds AUTH/WATCHLIST/ADMIN at module scope and
# each reads os.environ["DB_URL"] in __init__, so importing it without the
# environment already populated raises KeyError at import time. That is what
# killed this script on 2026-07-09, when the engine dropped its hard-coded
# DB_URL fallback (correctly -- it embedded a password) and the bare
# os.environ[...] lookup became fatal. systemd gives the engine its environment
# via EnvironmentFile; cron does not, so the .env parse must happen before the
# import and the values must land in os.environ, not just a local dict.
ENV = {}
for line in open("/opt/cas/.env"):
    line = line.strip()
    if "=" in line and not line.startswith("#"):
        k, v = line.split("=", 1)
        ENV[k] = v
        os.environ.setdefault(k, v.strip().strip('"').strip("'"))

DB_URL = ENV.get("DB_URL", "")
if not DB_URL:
    print("ERROR: DB_URL not found in .env")
    sys.exit(1)

# Import cascade engine from cas_engine (after env is in place)
sys.path.insert(0, "/opt/cas")
try:
    from cas_engine import compute_cascade_maneuver
    CASCADE_AVAILABLE = True
except Exception as _imp_e:
    # Deliberately broad: the 2026-07-09 failure was a KeyError, which the
    # previous `except ImportError` did not catch, so the script died at the
    # import instead of degrading. Cascade is an enrichment step -- decisions
    # are worth computing without it -- but the reason must be visible.
    CASCADE_AVAILABLE = False
    print(f"  WARN: cascade engine not importable: {type(_imp_e).__name__}: {_imp_e}")

# ── Pc calculation (from engine) ──
def _bessel_i0(x):
    if x == 0: return 1.0
    ax = abs(x)
    if ax < 3.75:
        y = (x / 3.75) ** 2
        return 1.0 + y*(3.5156229 + y*(3.0899424 + y*(1.2067492 + y*(0.2659732 + y*(0.0360768 + y*0.0045813)))))
    else:
        y = 3.75 / ax
        if ax > 700: return 1e308
        return (math.exp(ax)/math.sqrt(ax))*(0.39894228 + y*(0.01328592 + y*(0.00225319 + y*(-0.00157565 + y*(0.00916281 + y*(-0.02057706 + y*(0.02635537 + y*(-0.01647633 + y*0.00392377))))))))

def collision_probability(miss_m, sigma=100.0, hbr=10.0):
    if sigma < 1e-3 or miss_m <= 0: return 0.0
    u = miss_m / sigma
    if u > 50: return 0.0
    s = hbr / sigma
    N = 200
    total = 0.0
    for k in range(N):
        theta = math.pi * k / N
        r = u * math.cos(theta)
        arg = s*s*0.5 - r*r*0.5
        if arg > 500: arg = 500
        try:
            val = math.exp(arg) * _bessel_i0(s * u * math.cos(theta))
        except OverflowError:
            val = 0.0
        total += val
    return min((s*s/(2.0*N))*total, 1.0)

def classify_risk(pc):
    if pc >= 1e-4: return "RED"
    if pc >= 1e-5: return "YELLOW"
    return "GREEN"

# ── Decision logic ──
def evaluate_conjunctions(conjunctions, sat_name, norad_id):
    if not conjunctions:
        return {
            "norad_id": norad_id, "sat_name": sat_name,
            "recommendation": "No action", "priority": "LOW", "confidence": "HIGH",
            "max_pc": 0, "min_miss_m": 0, "total_conjunctions": 0,
            "red_count": 0, "yellow_count": 0, "green_count": 0,
            "alert_total": 0, "alert_review": 0, "alert_critical": 0,
            "maneuver_summary": None, "delta_v_ms": None, "maneuver_direction": None,
        }

    red = [c for c in conjunctions if c.get("risk") == "RED"]
    yellow = [c for c in conjunctions if c.get("risk") == "YELLOW"]
    green = [c for c in conjunctions if c.get("risk") == "GREEN"]
    
    max_pc = max((c.get("pc", 0) or 0) for c in conjunctions)
    min_miss = min((c.get("miss_distance_m", 9999) or 9999) for c in conjunctions)
    
    # Decision logic
    if len(red) >= 2 or max_pc >= 1e-2:
        recommendation = "Maneuver advised"
        priority = "CRITICAL"
        confidence = "HIGH"
    elif len(red) >= 1 or max_pc >= 1e-3:
        recommendation = "Maneuver advised"
        priority = "HIGH"
        confidence = "MEDIUM"
    elif len(yellow) >= 2 or max_pc >= 1e-4:
        recommendation = "Monitor"
        priority = "MEDIUM"
        confidence = "MEDIUM"
    else:
        recommendation = "No action"
        priority = "LOW"
        confidence = "HIGH"

    # Simple maneuver estimate for execute recommendations
    maneuver_summary = None
    delta_v = None
    direction = None
    if recommendation == "Maneuver advised" and min_miss < 1000:
        delta_v = round(0.1 + (1000 - min_miss) * 0.001, 3)
        direction = "prograde"
        orbit_raise = max(50, int((1000 - min_miss) * 0.5))
        maneuver_summary = f"Raise orbit +{orbit_raise}m {direction}. Delta-V: {delta_v} m/s"

    return {
        "norad_id": norad_id, "sat_name": sat_name,
        "recommendation": recommendation, "priority": priority, "confidence": confidence,
        "max_pc": max_pc, "min_miss_m": min_miss,
        "total_conjunctions": len(conjunctions),
        "red_count": len(red), "yellow_count": len(yellow), "green_count": len(green),
        "alert_total": len(conjunctions),
        "alert_review": len(red) + len(yellow),
        "alert_critical": len(red),
        "maneuver_summary": maneuver_summary,
        "delta_v_ms": delta_v, "maneuver_direction": direction,
    }

# ── Main scanner ──
def scan_user(user_id):
    conn = psycopg2.connect(DB_URL)
    cur = conn.cursor()
    
    # Get watchlist
    cur.execute("""
        SELECT id, norad_id, sat_name, altitude_km, regime
        FROM watchlist WHERE user_id = %s
    """, (user_id,))
    satellites = cur.fetchall()
    
    if not satellites:
        print(f"  User {user_id}: no satellites")
        cur.close(); conn.close()
        return 0

    total_decisions = 0
    
    for sat in satellites:
        wl_id, norad_id, sat_name, alt_km, regime = sat
        
        # Get recent conjunctions for this satellite
        cur.execute("""
            SELECT cdm_id, sat1, sat2, norad1, norad2, tca, miss_dist_m, pc, risk
            FROM conjunction_events
            WHERE (norad1 = %s OR norad2 = %s)
            AND fetched_at > NOW() - INTERVAL '7 days'
            -- Only conjunctions that have not happened yet. The window above is
            -- on fetch time, not event time, so a CDM downloaded three days ago
            -- for an encounter that passed yesterday stayed in scope: it fed
            -- max_pc, red_count and alert_critical, and min(tca) picked it as
            -- the earliest TCA. That produced "Maneuver advised / HIGH /
            -- PASSED (8h ago)" for IMECE on 2026-08-16 -- a maneuver
            -- recommendation for an encounter already behind us, alongside an
            -- inflated critical-alert count. Of the 509 events in the last
            -- seven days, 432 are already past, so the exposure is structural
            -- even though no watchlist object happens to hit it right now.
            -- Past encounters remain in conjunction_events for history; they
            -- just no longer drive a forward-looking decision.
            AND tca > NOW()
            ORDER BY pc DESC
        """, (norad_id, norad_id))
        
        conj_rows = cur.fetchall()
        conjunctions = []
        seen_cdms = set()
        for r in conj_rows:
            if r[0] in seen_cdms:
                continue
            seen_cdms.add(r[0])
            conjunctions.append({
                "cdm_id": r[0], "sat1": r[1], "sat2": r[2],
                "norad1": r[3], "norad2": r[4],
                "tca": r[5].isoformat() if r[5] else None,
                "miss_distance_m": float(r[6]) if r[6] else 0,
                "pc": float(r[7]) if r[7] else 0,
                "risk": r[8],
            })
        
        # Evaluate
        decision = evaluate_conjunctions(conjunctions, sat_name, norad_id)
        
        # Find earliest TCA
        tcas = [c["tca"] for c in conjunctions if c.get("tca")]
        earliest_tca = min(tcas) if tcas else None
        
        # Time remaining
        time_remaining_s = None
        time_remaining_str = None
        if earliest_tca:
            try:
                tca_dt = datetime.datetime.fromisoformat(earliest_tca)
                now = datetime.datetime.now(tca_dt.tzinfo) if tca_dt.tzinfo else datetime.datetime.now()
                delta = (tca_dt - now).total_seconds()
                if delta > 0:
                    time_remaining_s = delta
                    hours = int(delta // 3600)
                    if hours >= 24:
                        time_remaining_str = f"{hours//24}d {hours%24}h"
                    elif hours > 0:
                        time_remaining_str = f"{hours}h {int((delta%3600)//60)}m"
                    else:
                        time_remaining_str = f"{int(delta//60)}m"
                else:
                    time_remaining_s = 0
                    hours_ago = int(abs(delta) // 3600)
                    if hours_ago >= 24:
                        time_remaining_str = f"PASSED ({hours_ago//24}d ago)"
                    else:
                        time_remaining_str = f"PASSED ({hours_ago}h ago)"
            except Exception:
                pass

        # ── Cascade Analysis ──
        cascade_result = None
        if CASCADE_AVAILABLE and decision["recommendation"] == "Maneuver advised":
            try:
                # Get TLE data for this satellite
                tle1 = None
                tle2 = None
                cur.execute("SELECT tle_line1, tle_line2 FROM watchlist WHERE user_id=%s AND norad_id=%s", (user_id, norad_id))
                tle_row = cur.fetchone()
                if tle_row:
                    tle1, tle2 = tle_row[0], tle_row[1]
                
                cascade_result = compute_cascade_maneuver(
                    decision["min_miss_m"],
                    "RED" if decision["red_count"] > 0 else "YELLOW",
                    active_conjunctions=conjunctions,
                    sigma=100.0,
                    sat_name=sat_name,
                    sat_line1=tle1,
                    sat_line2=tle2
                )
            except Exception as ce:
                cascade_result = {"error": str(ce)}
        
        # Update maneuver summary with cascade info
        if cascade_result and not cascade_result.get("error"):
            ca = cascade_result.get("cascade_analysis", {})
            if ca.get("performed"):
                objects_checked = ca.get("catalog_objects_checked", 0)
                is_safe = ca.get("is_safe", True)
                sec_count = ca.get("secondary_risk_count", 0)
                cascade_score = ca.get("cascade_score", 0)
                
                if is_safe:
                    cascade_note = f"CASCADE CLEAR: {objects_checked} objects screened, no secondary collision risks detected."
                else:
                    cascade_note = f"CASCADE WARNING: {objects_checked} objects screened, {sec_count} secondary risks identified (score: {cascade_score:.1f})."
                
                # Update maneuver summary
                if decision["maneuver_summary"]:
                    decision["maneuver_summary"] += "\n" + cascade_note
                else:
                    decision["maneuver_summary"] = cascade_note
                
                # Update delta_v from cascade-optimized result
                if cascade_result.get("delta_v_ms"):
                    decision["delta_v_ms"] = cascade_result["delta_v_ms"]
                if cascade_result.get("direction"):
                    decision["maneuver_direction"] = cascade_result["direction"]
            else:
                # v1 fallback was used
                decision["maneuver_summary"] = (decision["maneuver_summary"] or "") + "\nCascade: basic cross-check performed (no TLE data for full catalog screening)."

        # Upsert decision
        cur.execute("""
            INSERT INTO decision_results (
                user_id, watchlist_id, norad_id, sat_name,
                recommendation, priority, confidence,
                max_pc, min_miss_m, total_conjunctions,
                red_count, yellow_count, green_count,
                tca_earliest, time_remaining_s, time_remaining_str,
                maneuver_summary, delta_v_ms, maneuver_direction,
                alert_total, alert_review, alert_critical,
                cascade_result, computed_at, expires_at
            ) VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW(), NOW() + INTERVAL '9 hours'
            )
            ON CONFLICT (user_id, norad_id) DO UPDATE SET
                recommendation = EXCLUDED.recommendation,
                priority = EXCLUDED.priority,
                confidence = EXCLUDED.confidence,
                max_pc = EXCLUDED.max_pc,
                min_miss_m = EXCLUDED.min_miss_m,
                total_conjunctions = EXCLUDED.total_conjunctions,
                red_count = EXCLUDED.red_count,
                yellow_count = EXCLUDED.yellow_count,
                green_count = EXCLUDED.green_count,
                tca_earliest = EXCLUDED.tca_earliest,
                time_remaining_s = EXCLUDED.time_remaining_s,
                time_remaining_str = EXCLUDED.time_remaining_str,
                maneuver_summary = EXCLUDED.maneuver_summary,
                delta_v_ms = EXCLUDED.delta_v_ms,
                maneuver_direction = EXCLUDED.maneuver_direction,
                alert_total = EXCLUDED.alert_total,
                alert_review = EXCLUDED.alert_review,
                alert_critical = EXCLUDED.alert_critical,
                cascade_result = EXCLUDED.cascade_result,
                computed_at = NOW(),
                expires_at = NOW() + INTERVAL '9 hours'
        """, (
            user_id, wl_id, norad_id, sat_name,
            decision["recommendation"], decision["priority"], decision["confidence"],
            decision["max_pc"], decision["min_miss_m"], decision["total_conjunctions"],
            decision["red_count"], decision["yellow_count"], decision["green_count"],
            earliest_tca, time_remaining_s, time_remaining_str,
            decision["maneuver_summary"], decision["delta_v_ms"], decision["maneuver_direction"],
            decision["alert_total"], decision["alert_review"], decision["alert_critical"],
            json.dumps(cascade_result) if cascade_result else None,
        ))
        total_decisions += 1
    
    conn.commit()
    cur.close()
    conn.close()
    return total_decisions

# ── Entry point ──
if __name__ == "__main__":
    print("=" * 50)
    print("  CAS Decision Scanner (Standalone)")
    print("=" * 50)
    
    conn = psycopg2.connect(DB_URL)
    cur = conn.cursor()
    
    # Check if unique constraint exists
    cur.execute("""
        SELECT 1 FROM pg_constraint 
        WHERE conname = 'decision_results_user_norad_uq'
    """)
    if not cur.fetchone():
        # Add unique constraint for upsert
        try:
            cur.execute("""
                DELETE FROM decision_results a USING decision_results b
                WHERE a.id < b.id AND a.user_id = b.user_id AND a.norad_id = b.norad_id
            """)
            cur.execute("""
                ALTER TABLE decision_results 
                ADD CONSTRAINT decision_results_user_norad_uq 
                UNIQUE (user_id, norad_id)
            """)
            conn.commit()
            print("  DB: unique constraint added")
        except Exception as e:
            conn.rollback()
            print(f"  DB: constraint note: {e}")
    
    # Get all users with watchlist
    cur.execute("SELECT DISTINCT user_id FROM watchlist")
    users = [r[0] for r in cur.fetchall()]
    cur.close(); conn.close()
    
    print(f"  Users with watchlist: {len(users)}")
    
    total = 0
    for uid in users:
        t0 = time.time()
        count = scan_user(uid)
        elapsed = time.time() - t0
        print(f"  User {uid}: {count} decisions in {elapsed:.1f}s")
        total += count
    
    print(f"\n  Total: {total} decisions generated")
    print("=" * 50)
