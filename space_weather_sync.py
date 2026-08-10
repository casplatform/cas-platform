#!/usr/bin/env python3
"""NOAA SWPC sync — fetch latest space weather snapshot, save to DB."""
import os, sys, json, urllib.request, urllib.error
from pathlib import Path

# Load .env
for line in Path("/opt/cas/.env").read_text().splitlines():
    if "=" in line and not line.startswith("#"):
        k, v = line.split("=", 1)
        os.environ[k.strip()] = v.strip().strip('"').strip("'")

import psycopg2

# Central data-health tracking (empty data must not overwrite last good data)
sys.path.insert(0, "/opt/cas/cas_api")
try:
    from core.data_health import report_success, report_failure
except Exception as _dh_e:
    print(f"[SWX] data_health import failed ({_dh_e}); health tracking disabled")
    def report_success(*a, **k): pass
    def report_failure(*a, **k): pass

def fetch_json(url, timeout=10):
    req = urllib.request.Request(url, headers={"User-Agent": "CAS-Platform/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))

def kp_status(kp):
    if kp is None: return "unknown"
    if kp < 4: return "quiet"
    if kp < 5: return "unsettled"
    if kp < 6: return "G1 minor"
    if kp < 7: return "G2 moderate"
    if kp < 8: return "G3 strong"
    if kp < 9: return "G4 severe"
    return "G5 extreme"

def f107_status(flux):
    if flux is None: return "unknown"
    if flux < 80: return "low"
    if flux < 120: return "moderate"
    if flux < 180: return "elevated"
    return "high"

def xray_class_from_flux(flux):
    if flux is None or flux <= 0: return "A0", "quiet"
    if flux < 1e-7: return f"A{flux/1e-8:.1f}", "quiet"
    if flux < 1e-6: return f"B{flux/1e-7:.1f}", "minor"
    if flux < 1e-5: return f"C{flux/1e-6:.1f}", "minor"
    if flux < 1e-4: return f"M{flux/1e-5:.1f}", "moderate (R1-R2)"
    return f"X{flux/1e-4:.1f}", "strong (R3+)"

def main():
    snap = {
        "kp_index": None, "kp_estimated": None, "kp_label": None, "kp_status": "unknown",
        "f107_flux": None, "f107_status": "unknown",
        "xray_class": None, "xray_flux_peak": None, "xray_status": "unknown",
        "active_alerts": [], "raw_summary": {},
    }
    errors = []

    # Kp
    try:
        kp_data = fetch_json("https://services.swpc.noaa.gov/json/planetary_k_index_1m.json")
        if kp_data:
            latest = kp_data[-1]
            snap["kp_index"] = float(latest.get("kp_index")) if latest.get("kp_index") is not None else None
            snap["kp_estimated"] = float(latest.get("estimated_kp")) if latest.get("estimated_kp") is not None else None
            snap["kp_label"] = latest.get("kp")
            snap["kp_status"] = kp_status(snap["kp_index"])
            snap["raw_summary"]["kp_time"] = latest.get("time_tag")
    except Exception as e:
        errors.append(f"kp: {e}")

    # F10.7
    try:
        f107_data = fetch_json("https://services.swpc.noaa.gov/json/f107_cm_flux.json")
        if f107_data:
            latest = f107_data[0]  # first is most recent in this endpoint
            snap["f107_flux"] = float(latest.get("flux")) if latest.get("flux") is not None else None
            snap["f107_status"] = f107_status(snap["f107_flux"])
            snap["raw_summary"]["f107_time"] = latest.get("time_tag")
            snap["raw_summary"]["f107_90day_mean"] = latest.get("ninety_day_mean")
    except Exception as e:
        errors.append(f"f107: {e}")

    # X-ray (find max in last day, only 0.1-0.8nm channel = standard solar flare classification)
    try:
        xray_data = fetch_json("https://services.swpc.noaa.gov/json/goes/primary/xrays-1-day.json")
        long_band = [d for d in xray_data if d.get("energy") == "0.1-0.8nm"]
        if long_band:
            peak = max(long_band, key=lambda d: d.get("flux") or 0)
            flux = peak.get("flux")
            cls, status = xray_class_from_flux(flux)
            snap["xray_class"] = cls
            snap["xray_flux_peak"] = flux
            snap["xray_status"] = status
            snap["raw_summary"]["xray_peak_time"] = peak.get("time_tag")
    except Exception as e:
        errors.append(f"xray: {e}")

    # Alerts (last 6 hours)
    try:
        alerts_data = fetch_json("https://services.swpc.noaa.gov/products/alerts.json")
        # Keep only last 10 most recent
        recent = alerts_data[:10] if isinstance(alerts_data, list) else []
        # Strip to compact summary (first line of each message)
        compact = []
        for a in recent:
            msg = (a.get("message") or "").split("\r\n")
            # Find ALERT/SUMMARY/WATCH/WARNING line
            title = next((l for l in msg if any(k in l for k in ("ALERT:", "SUMMARY:", "WATCH:", "WARNING:", "CANCEL"))), msg[0] if msg else "")
            compact.append({
                "issued": a.get("issue_datetime"),
                "code": a.get("product_id"),
                "title": title.strip(),
            })
        snap["active_alerts"] = compact
    except Exception as e:
        errors.append(f"alerts: {e}")

    if errors:
        snap["raw_summary"]["errors"] = errors

    # Data-health guard: if NOTHING usable came back (all core metrics None),
    # the upstream feed is broken. Do NOT insert an empty row over the last
    # good data — record the failure (mails once) and keep the old snapshot.
    data_ok = (snap["kp_index"] is not None
               or snap["f107_flux"] is not None
               or snap["xray_class"] is not None)
    if not data_ok:
        report_failure("space_weather", f"NOAA returned no usable data: {errors}")
        print("[SWX] SKIP insert — upstream empty, last good snapshot preserved", flush=True)
        return

    # Insert
    conn = psycopg2.connect(os.environ["DB_URL"])
    cur = conn.cursor()
    cur.execute(
        """INSERT INTO space_weather_snapshots
           (kp_index, kp_estimated, kp_label, kp_status,
            f107_flux, f107_status,
            xray_class, xray_flux_peak, xray_status,
            active_alerts, raw_summary)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
           RETURNING id, fetched_at""",
        (snap["kp_index"], snap["kp_estimated"], snap["kp_label"], snap["kp_status"],
         snap["f107_flux"], snap["f107_status"],
         snap["xray_class"], snap["xray_flux_peak"], snap["xray_status"],
         json.dumps(snap["active_alerts"]), json.dumps(snap["raw_summary"])),
    )
    sid, ftc = cur.fetchone()
    conn.commit()
    cur.close()
    conn.close()
    print(f"[SWX] Snapshot id={sid} at {ftc} | Kp={snap['kp_index']} ({snap['kp_status']}) | F10.7={snap['f107_flux']} ({snap['f107_status']}) | X-ray={snap['xray_class']} ({snap['xray_status']}) | alerts={len(snap['active_alerts'])}", flush=True)
    if errors:
        print(f"[SWX] WARNINGS: {errors}", flush=True)

    # Good data was stored — mark source healthy (sends recovery mail if it
    # had been failing).
    report_success("space_weather")

if __name__ == "__main__":
    main()
