"""
CAS Layer 1 ML Scoring Module
==============================

Production-ready inference wrapper for xgb_layer1_baseline.

Usage:
    from cas.ml.scoring import Layer1Scorer
    scorer = Layer1Scorer()  # singleton, model bir kere yuklenir
    result = scorer.score(cdm_features_dict)
    # result = {
    #   "tier": "RED" | "YELLOW" | "GREEN",
    #   "ml_score": 0.234,    # raw probability
    #   "red_threshold": 0.137,
    #   "yellow_threshold": 0.051,
    #   "version": "baseline-v2-no-leakage"
    # }

Bu modul CAS engine'den asagidaki sekilde cagrilabilir:
  - Decision scanner'da her CDM scoring sonrasi ML tier de hesaplanir
  - /api/ml/score endpoint'inde (admin only ilk asamada)
  - watchlist_results tablosuna ml_tier kolonu eklendiginde
"""
import os
import json
import warnings
import numpy as np
import pandas as pd
import xgboost as xgb

ML_BASE = "/opt/cas/ml"
MODELS_DIR = f"{ML_BASE}/models"


class Layer1Scorer:
    """Singleton XGBoost scorer for Layer 1 (false positive reduction)."""

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._loaded = False
        return cls._instance

    def __init__(self):
        if self._loaded:
            return

        # Production bundle
        with open(f"{MODELS_DIR}/xgb_layer1_production.json") as f:
            self.production = json.load(f)

        # Model
        self.model = xgb.XGBClassifier()
        self.model.load_model(f"{MODELS_DIR}/{self.production['model_path']}")

        # Feature list
        with open(f"{MODELS_DIR}/{self.production['features_path']}") as f:
            feat_meta = json.load(f)
        self.expected_features = feat_meta["features"]

        self.red_thr = self.production["thresholds"]["red"]
        self.yel_thr = self.production["thresholds"]["yellow"]
        self.version = self.production["model_version"]

        self._loaded = True

    def _classify_tier(self, proba):
        # FIX: red_thr > yel_thr now (bundle düzeltildikten sonra)
        if proba >= self.red_thr:
            return "RED"
        elif proba >= self.yel_thr:
            return "YELLOW"
        return "GREEN"

    def _preprocess(self, features_dict):
        """Convert features dict to model-ready DataFrame with same pipeline as training."""
        # Tek-row DataFrame yap
        X = pd.DataFrame([features_dict])

        # inf -> NaN
        X = X.replace([np.inf, -np.inf], np.nan)

        # log10(covariance_det)
        for col in ["t_position_covariance_det", "c_position_covariance_det"]:
            if col in X.columns and pd.notna(X[col].iloc[0]):
                val = X[col].iloc[0]
                X[col] = np.log10(abs(val)) if val != 0 else np.nan

        # float32 overflow defense
        F32_MAX = np.finfo(np.float32).max
        for col in X.select_dtypes(include=np.number).columns:
            if X[col].abs().max() > F32_MAX:
                X.loc[X[col].abs() > F32_MAX, col] = np.nan

        # Feature alignment - eksik kolon NaN, fazla cikar
        for f in self.expected_features:
            if f not in X.columns:
                X[f] = np.nan
        X = X[self.expected_features]

        return X

    def score(self, features_dict):
        """
        Score a single CDM/event.

        Args:
            features_dict: dict {feature_name: value}
                Expected keys include: miss_distance, time_to_tca, relative_speed,
                t_sigma_r, c_sigma_t, mahalanobis_distance, t_h_apo, vs.

        Returns:
            {"tier": str, "ml_score": float, "red_threshold": float,
             "yellow_threshold": float, "version": str}
        """
        X = self._preprocess(features_dict)

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            proba = float(self.model.predict_proba(X)[0, 1])

        tier = self._classify_tier(proba)
        return {
            "tier": tier,
            "ml_score": proba,
            "red_threshold": self.red_thr,
            "yellow_threshold": self.yel_thr,
            "version": self.version
        }

    def score_batch(self, df):
        """Vectorized batch scoring for a DataFrame."""
        X = df.copy()
        # Same preprocessing (vectorized)
        X = X.replace([np.inf, -np.inf], np.nan)
        for col in ["t_position_covariance_det", "c_position_covariance_det"]:
            if col in X.columns:
                X[col] = np.log10(X[col].abs().replace(0, np.nan))

        F32_MAX = np.finfo(np.float32).max
        for col in X.select_dtypes(include=np.number).columns:
            if X[col].abs().max() > F32_MAX:
                X.loc[X[col].abs() > F32_MAX, col] = np.nan

        for f in self.expected_features:
            if f not in X.columns:
                X[f] = np.nan
        X = X[self.expected_features]

        probas = self.model.predict_proba(X)[:, 1]
        tiers = [self._classify_tier(p) for p in probas]
        return pd.DataFrame({"ml_score": probas, "ml_tier": tiers})


# Module-level convenience
_scorer = None

def get_scorer():
    """Lazy singleton getter."""
    global _scorer
    if _scorer is None:
        _scorer = Layer1Scorer()
    return _scorer
