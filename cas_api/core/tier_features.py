"""Tier feature-access map for FastAPI enforcement.

⚠️  SINGLE-SOURCE NOTE — KEEP IN SYNC WITH cas_engine.py TierConfig.TIERS
    This is a deliberate, minimal MIRROR of the *feature-access* portion of
    the engine's TierConfig. We do NOT import cas_engine here because importing
    it triggers engine module-level side effects (watchlist scanner, email
    manager) and expects DB_URL in os.environ — neither of which is appropriate
    inside the FastAPI process. Prices/descriptions live ONLY in the engine.
    If you change feature access in cas_engine.py TierConfig, mirror it here.

    Last synced: 2026-07-08 (Explorer/Starter/Pro/Enterprise).
"""
from typing import Dict, Any

# feature-access matrix — mirrors cas_engine.py TierConfig (feature flags only)
_TIER_FEATURES: Dict[str, Dict[str, Any]] = {
    "free": {
        "ml_access": False,
        "maneuver_access": False,
        "realtime_data": False,
        "vleo_access": False,
        "mission_design_access": False,
        "reporting_level": "none",
        "api_access": False,
        "trend_access": False,
        "cascade_access": False,
        "insurance_access": False,
        "portfolio_access": False,
    },
    "starter": {
        "ml_access": False,
        "maneuver_access": False,
        "realtime_data": True,
        "vleo_access": False,
        "mission_design_access": False,
        "reporting_level": "monthly",
        "api_access": True,
        "trend_access": True,
        "cascade_access": "limited",
        "insurance_access": False,
        "portfolio_access": False,
    },
    "pro": {
        "ml_access": True,
        "maneuver_access": True,
        "realtime_data": True,
        "vleo_access": True,
        "mission_design_access": True,
        "reporting_level": "full",
        "api_access": True,
        "trend_access": True,
        "cascade_access": True,
        "insurance_access": False,
        "portfolio_access": False,
    },
    "enterprise": {
        "ml_access": True,
        "maneuver_access": True,
        "realtime_data": True,
        "vleo_access": True,
        "mission_design_access": True,
        "reporting_level": "full",
        "api_access": True,
        "trend_access": True,
        "cascade_access": True,
        "insurance_access": False,
        "portfolio_access": False,
    },

    # ── INSURANCE TIERS (separate product line, role='insurer') ──
    # Mutually exclusive with operator tiers: insurers see no operator modules,
    # operators see no insurance modules.
    "insurer_demo": {
        "watch_limit": 0,
        "ml_access": False, "maneuver_access": False, "realtime_data": False,
        "vleo_access": False, "mission_design_access": False,
        "reporting_level": "watermarked", "api_access": False,
        "trend_access": True, "cascade_access": True,
        "insurance_access": "synthetic",   # demo scenarios only, no real data
        "portfolio_access": False,
    },
    "insurer_starter": {
        "watch_limit": 0,
        "ml_access": False, "maneuver_access": False, "realtime_data": True,
        "vleo_access": False, "mission_design_access": False,
        "reporting_level": "full", "api_access": False,
        "trend_access": True, "cascade_access": True,
        "insurance_access": True,
        "portfolio_access": False,
    },
    "insurer_pro": {
        "watch_limit": 5,
        "ml_access": False, "maneuver_access": False, "realtime_data": True,
        "vleo_access": False, "mission_design_access": False,
        "reporting_level": "full", "api_access": False,
        "trend_access": True, "cascade_access": True,
        "insurance_access": True,
        "portfolio_access": True,
    },
    "insurer_enterprise": {
        "watch_limit": -1,
        "ml_access": False, "maneuver_access": False, "realtime_data": True,
        "vleo_access": False, "mission_design_access": False,
        "reporting_level": "custom", "api_access": True,
        "trend_access": True, "cascade_access": True,
        "insurance_access": True,
        "portfolio_access": True,
    },
}

# human-readable tier names (for 403 messages)
_TIER_NAMES = {"free": "Explorer", "starter": "Starter",
               "pro": "Pro", "enterprise": "Enterprise"}


def has_feature(tier: str, feature: str) -> bool:
    """True if the given tier may access the boolean feature."""
    t = _TIER_FEATURES.get(tier, _TIER_FEATURES["free"])
    val = t.get(feature)
    if isinstance(val, bool):
        return val
    # non-bool (e.g. reporting_level) — treat any non-"none" as access
    if isinstance(val, str):
        return val != "none"
    return False


def min_tier_for(feature: str) -> str:
    """Lowest tier name that grants this feature (for upgrade messaging)."""
    for tier in ("starter", "pro", "enterprise"):
        if has_feature(tier, feature):
            return _TIER_NAMES[tier]
    return _TIER_NAMES["enterprise"]


def tier_name(tier: str) -> str:
    return _TIER_NAMES.get(tier, tier)


# ---- Reporting: graded level (none < monthly < full) ----
_REPORT_RANK = {"none": 0, "monthly": 1, "full": 2}


def reporting_rank(tier: str) -> int:
    """Numeric rank of a tier's reporting_level."""
    lvl = _TIER_FEATURES.get(tier, _TIER_FEATURES["free"]).get("reporting_level", "none")
    return _REPORT_RANK.get(lvl, 0)


def has_reporting(tier: str, required_level: str) -> bool:
    """True if tier's reporting_level >= required_level."""
    return reporting_rank(tier) >= _REPORT_RANK.get(required_level, 99)


def min_tier_for_reporting(required_level: str) -> str:
    """Lowest tier granting the required reporting level."""
    need = _REPORT_RANK.get(required_level, 99)
    for tier in ("starter", "pro", "enterprise"):
        if reporting_rank(tier) >= need:
            return _TIER_NAMES[tier]
    return _TIER_NAMES["enterprise"]
