"""Synthetic scenarios for demo accounts.

A demo account must show the product honestly without giving away the product:
the physics and the formula are identical, but the objects are fictional and the
figures are derived from a frozen snapshot — never from the live catalogue.
Nobody gets a free real assessment, and nobody mistakes a demo sheet for a real
one (every output carries a watermark).
"""
from typing import Any, Dict, List, Optional

DEMO_SCENARIOS: Dict[str, Dict[str, Any]] = {
    "DEMO-SAT-A": {
        "label": "DEMO-SAT-A", "altitude_km": 550.0, "inclination_deg": 53.0,
        "note": "Mega-constellation shell — dense but largely active, manoeuvrable traffic",
        "threat": 7, "total": 2077, "debris": 5, "rocket_body": 2, "payload": 2070,
        "threat_fraction_pct": 0.3, "lambda": 7.07e-7, "rho": 2.32e-10,
        "v_rel": 9.66, "trend_pct": 20.1, "trend_first": 3, "trend_last": 9,
        "cascade_pool": 3060, "cascade_years": 5.9,
    },
    "DEMO-SAT-B": {
        "label": "DEMO-SAT-B", "altitude_km": 800.0, "inclination_deg": 98.6,
        "note": "Sun-synchronous peak — the densest non-manoeuvrable population in LEO",
        "threat": 1050, "total": 1233, "debris": 1013, "rocket_body": 37, "payload": 183,
        "threat_fraction_pct": 85.2, "lambda": 9.71e-5, "rho": 3.24e-8,
        "v_rel": 9.49, "trend_pct": 2.7, "trend_first": 810, "trend_last": 949,
        "cascade_pool": 3268, "cascade_years": 100.3,
    },
    "DEMO-SAT-C": {
        "label": "DEMO-SAT-C", "altitude_km": 620.0, "inclination_deg": 97.8,
        "note": "Lower sun-synchronous — moderate today, fastest-growing threat population",
        "threat": 303, "total": 802, "debris": 281, "rocket_body": 22, "payload": 499,
        "threat_fraction_pct": 37.8, "lambda": 2.99e-5, "rho": 9.98e-9,
        "v_rel": 9.61, "trend_pct": 17.8, "trend_first": 86, "trend_last": 266,
        "cascade_pool": 2424, "cascade_years": 13.1,
    },
    "DEMO-SAT-D": {
        "label": "DEMO-SAT-D", "altitude_km": 1000.0, "inclination_deg": 99.0,
        "note": "High LEO — debris persists for centuries; population slowly declining",
        "threat": 349, "total": 787, "debris": 309, "rocket_body": 40, "payload": 438,
        "threat_fraction_pct": 44.3, "lambda": 3.01e-5, "rho": 1.02e-8,
        "v_rel": 9.36, "trend_pct": -3.5, "trend_first": 392, "trend_last": 317,
        "cascade_pool": 1469, "cascade_years": 100.0,
    },
    "DEMO-SAT-E": {
        "label": "DEMO-SAT-E", "altitude_km": 450.0, "inclination_deg": 51.6,
        "note": "VLEO — drag clears debris within months; lowest persistent exposure",
        "threat": 24, "total": 1180, "debris": 20, "rocket_body": 4, "payload": 1156,
        "threat_fraction_pct": 2.0, "lambda": 2.61e-6, "rho": 8.51e-10,
        "v_rel": 9.74, "trend_pct": 11.2, "trend_first": 12, "trend_last": 24,
        "cascade_pool": 4210, "cascade_years": 1.4,
    },
    "DEMO-SAT-F": {
        "label": "DEMO-SAT-F", "altitude_km": 750.0, "inclination_deg": 98.0,
        "note": "Upper sun-synchronous — high burden with a steady upward trend",
        "threat": 734, "total": 1319, "debris": 698, "rocket_body": 36, "payload": 585,
        "threat_fraction_pct": 55.6, "lambda": 6.91e-5, "rho": 2.30e-8,
        "v_rel": 9.52, "trend_pct": 9.6, "trend_first": 399, "trend_last": 691,
        "cascade_pool": 2720, "cascade_years": 100.0,
    },
}

DEMO_FLEET: List[Dict[str, Any]] = [
    {"label": "FLEET-01", "scenario": "DEMO-SAT-B"},
    {"label": "FLEET-02", "scenario": "DEMO-SAT-F"},
    {"label": "FLEET-03", "scenario": "DEMO-SAT-C"},
    {"label": "FLEET-04", "scenario": "DEMO-SAT-A"},
    {"label": "FLEET-05", "scenario": "DEMO-SAT-D"},
    {"label": "FLEET-06", "scenario": "DEMO-SAT-E"},
]


def list_scenarios() -> List[Dict[str, Any]]:
    return [{"id": k, "label": v["label"], "altitude_km": v["altitude_km"],
             "inclination_deg": v["inclination_deg"], "note": v["note"]}
            for k, v in DEMO_SCENARIOS.items()]


def assess(scenario_id: str) -> Dict[str, Any]:
    """Build an assessment payload in the exact shape of the real one."""
    s = DEMO_SCENARIOS.get((scenario_id or "").upper())
    if s is None:
        raise ValueError(f"Unknown demo scenario '{scenario_id}'. "
                         f"Available: {', '.join(DEMO_SCENARIOS)}")
    return {
        "orbit": {"altitude_km": s["altitude_km"],
                  "inclination_deg": s["inclination_deg"], "band_half_km": 25.0},
        "subject": {"norad": None, "name": s["label"],
                    "altitude_km": s["altitude_km"],
                    "inclination_deg": s["inclination_deg"],
                    "object_type": "synthetic"},
        "catalogue": {
            "total": s["total"], "threat": s["threat"], "debris": s["debris"],
            "rocket_body": s["rocket_body"], "payload": s["payload"],
            "threat_fraction_pct": s["threat_fraction_pct"],
            "band_km": [s["altitude_km"] - 25, s["altitude_km"] + 25],
            "inclination_gated": True,
        },
        "burden": {
            "rho_threat_per_km3": s["rho"], "v_rel_mean_km_s": s["v_rel"],
            "shell_volume_km3": None, "lambda_per_year": s["lambda"],
            "years_between_expected": (1.0 / s["lambda"]) if s["lambda"] else None,
            "reference_area_m2": 10.0,
        },
        "trend": {
            "primary": {"available": True, "mode": "inclination-gated",
                        "first_year": 2020, "last_year": 2026,
                        "threat_first": s["trend_first"], "threat_last": s["trend_last"],
                        "cagr_pct_per_year": s["trend_pct"],
                        "source": "Synthetic demo scenario"},
            "inclination_gated": {"available": True,
                                  "cagr_pct_per_year": s["trend_pct"]},
            "band_based": {"available": True,
                           "cagr_pct_per_year": round(s["trend_pct"] * 0.85, 1)},
        },
        "cascade": {
            "exposed_pool": s["cascade_pool"], "band_half_km": 50.0,
            "cloud_clearing_days_90pct": s["cascade_years"] * 365.25,
            "cloud_clearing_years_90pct": s["cascade_years"],
            "model": "NASA Standard Breakup Model + atmospheric drag",
        },
        "debris_flux": {
            "available": False, "model": None,
            "note": ("Lethal non-trackable (1-10 cm) flux requires ESA MASTER-8 "
                     "or NASA ORDEM. Not included in this assessment."),
        },
        "boundaries": {
            "is": ("SYNTHETIC DEMO SCENARIO — the physics and formula match the "
                   "production system, but the objects are fictional."),
            "is_not": ("Not real catalogue data. Not a satellite-specific collision "
                       "probability, not a premium, not an actuarial rate. This "
                       "sheet must not be used for underwriting."),
            "coverage": "Demo scenario only. No relation to any real orbital object.",
        },
        "scenario_id": (scenario_id or "").upper(),
        "scenario_note": s["note"],
    }


def fleet() -> List[Dict[str, Any]]:
    """Portfolio demo: rows in the same shape the portfolio endpoint returns."""
    out = []
    for f in DEMO_FLEET:
        s = DEMO_SCENARIOS[f["scenario"]]
        out.append({"label": f["label"], "altitude_km": s["altitude_km"],
                    "inclination_deg": s["inclination_deg"],
                    "threat_objects": s["threat"],
                    "lambda_per_year": s["lambda"],
                    "trend_pct_per_year": s["trend_pct"]})
    return out
