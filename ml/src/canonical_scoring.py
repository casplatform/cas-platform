"""CAS Layer 1 — Canonical inference scorer with coverage gate (G1).

Production scoring path:
    production CDM(s) -> source mapper -> CanonicalCDM -> feature_extractor
    -> COVERAGE GATE (G1) -> (if sufficient) XGBoost -> tier

The gate is the safety mechanism: Layer 1 was trained on RICH CDMs (full
covariance, sigmas, orbital elements). A sparse source (e.g. Space-Track public
16-field tier) yields a CanonicalCDM where most feature-bearing fields are
absent; scoring it would produce an imputation-driven number, not a physics-
grounded risk. The gate measures IMPORTANCE-WEIGHTED (gain) feature coverage and
returns tier=UNAVAILABLE below threshold, so the deterministic Pc funnel applies.

Takes a conjunction's CanonicalCDM history (list) — aggregate features need it.
"""
import os, sys, json, warnings
from typing import List
import numpy as np
import pandas as pd
import xgboost as xgb

if "/opt/cas/ml" not in sys.path:
    sys.path.insert(0, "/opt/cas/ml")
from src.feature_extractor import extract_event_features

MODELS = "/opt/cas/ml/models"
COVERAGE_THRESHOLD = 0.70   # min gain-weighted feature coverage to emit a score (calibratable)


class CanonicalLayer1Scorer:
    """Singleton canonical scorer with the G1 coverage gate."""
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls); cls._instance._loaded = False
        return cls._instance

    def __init__(self):
        if self._loaded:
            return
        with open(f"{MODELS}/xgb_layer1_canonical_production.json") as f:
            self.bundle = json.load(f)
        self.model = xgb.XGBClassifier()
        self.model.load_model(f"{MODELS}/{self.bundle['model_path']}")
        with open(f"{MODELS}/{self.bundle['features_path']}") as f:
            self.features = json.load(f)["features"]
        self.red_thr = self.bundle["thresholds"]["red"]      # higher (RED entry)
        self.yel_thr = self.bundle["thresholds"]["yellow"]   # lower  (YELLOW entry)
        self.version = self.bundle["model_version"]
        self.coverage_threshold = COVERAGE_THRESHOLD
        # gain importance per feature (0 for unused); drives the coverage gate
        gain = self.model.get_booster().get_score(importance_type="gain")
        self.importance = {f: float(gain.get(f, 0.0)) for f in self.features}
        self.total_importance = float(sum(self.importance.values())) or 1.0
        self._loaded = True

    def _coverage(self, feat):
        present = 0.0; missing = []
        for f, w in self.importance.items():
            v = feat.get(f)
            ok = not (v is None or (isinstance(v, float) and v != v))
            if ok:
                present += w
            elif w > 0:
                missing.append((f, w))
        missing.sort(key=lambda kv: -kv[1])
        return present / self.total_importance, missing

    def _tier(self, p):
        if p >= self.red_thr: return "RED"
        if p >= self.yel_thr: return "YELLOW"
        return "GREEN"

    def _preprocess(self, feat):
        # reindex to the trained feature set/order, then coerce to numeric. A
        # single-row frame with None values would otherwise infer object dtype
        # (XGBoost rejects object); training built a multi-row frame where None
        # inferred float64/NaN -- coercion reproduces that exactly (None -> NaN).
        X = pd.DataFrame([feat]).reindex(columns=self.features)
        X = X.apply(pd.to_numeric, errors="coerce")
        X = X.replace([np.inf, -np.inf], np.nan)
        for c in ("t_position_covariance_det", "c_position_covariance_det"):
            v = X[c].iloc[0]
            if pd.notna(v):
                X[c] = np.log10(abs(v)) if v != 0 else np.nan
        F32 = np.finfo(np.float32).max
        for c in X.columns:
            if X[c].abs().max() > F32:
                X.loc[X[c].abs() > F32, c] = np.nan
        return X

    def score(self, cdms: List) -> dict:
        """Score one conjunction's CanonicalCDM history. Gate-protected."""
        if not cdms:
            return {"tier": "UNAVAILABLE", "ml_score": None,
                    "reason": "no CDMs provided", "version": self.version}
        return self.score_features(extract_event_features(cdms))

    def score_features(self, feat: dict) -> dict:
        """Gate + score a pre-extracted feature dict (one conjunction).

        Split out from score() so a caller that also needs SHAP can extract the
        feature dict once and reuse it (and _preprocess) for the explainer.
        """
        cov, missing = self._coverage(feat)
        if cov < self.coverage_threshold:
            return {"tier": "UNAVAILABLE", "ml_score": None,
                    "coverage": round(cov, 3), "coverage_threshold": self.coverage_threshold,
                    "reason": "insufficient CDM feature coverage for reliable ML scoring; "
                              "deterministic Pc funnel applies",
                    "top_missing_features": [m[0] for m in missing[:8]],
                    "version": self.version}
        X = self._preprocess(feat)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            p = float(self.model.predict_proba(X)[0, 1])
        return {"tier": self._tier(p), "ml_score": p, "coverage": round(cov, 3),
                "red_threshold": self.red_thr, "yellow_threshold": self.yel_thr,
                "version": self.version}


_scorer = None
def get_canonical_scorer():
    global _scorer
    if _scorer is None:
        _scorer = CanonicalLayer1Scorer()
    return _scorer
