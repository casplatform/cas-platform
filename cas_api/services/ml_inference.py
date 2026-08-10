"""ML Inference Service — Canonical Layer 1 + G1 coverage gate + SHAP.

Production-honest pipeline (matches /opt/cas/ml/src/canonical_scoring.py):
    raw CDM(s) -> source mapper -> CanonicalCDM -> feature_extractor (107)
    -> G1 coverage gate -> (if sufficient) XGBoost canonical-v1 -> tier
    -> SHAP TreeExplainer top-N (only when scored)

The gate refuses to score sparse CDMs (e.g. Space-Track public 16-field),
returning tier=UNAVAILABLE so the deterministic Pc funnel applies. SHAP is
computed only for scored conjunctions (UNAVAILABLE has nothing to explain).
"""
import os, sys
from typing import Optional, Dict, List, Any
import numpy as np

ML_BASE = "/opt/cas/ml"
if ML_BASE not in sys.path:
    sys.path.insert(0, ML_BASE)


class MLInferenceService:
    """Singleton — canonical scorer + SHAP explainer loaded once."""
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._loaded = False
        return cls._instance

    def __init__(self):
        if self._loaded:
            return
        self.scorer = None
        self._extract = None
        self.shap_explainer = None
        self.shap_error: Optional[str] = None
        self.load_error: Optional[str] = None
        self._mappers: Dict[str, Any] = {}

        try:
            from src.canonical_scoring import get_canonical_scorer
            from src.feature_extractor import extract_event_features
            from mappers import SpaceTrackPublicMapper, CcsdsCdmMapper
            self.scorer = get_canonical_scorer()
            self._extract = extract_event_features
            self._mappers = {
                "spacetrack_public": SpaceTrackPublicMapper,
                "spacetrack": SpaceTrackPublicMapper,
                "ccsds_cdm": CcsdsCdmMapper,
                "ccsds": CcsdsCdmMapper,
                "starlink": CcsdsCdmMapper,
                "stargaze": CcsdsCdmMapper,
                "tracss": CcsdsCdmMapper,
            }
            print(f"[ML] CanonicalLayer1Scorer loaded (version={self.scorer.version}, "
                  f"{len(self.scorer.features)} features, "
                  f"coverage_gate={self.scorer.coverage_threshold})", flush=True)
        except Exception as e:
            self.load_error = f"{type(e).__name__}: {str(e)}"
            print(f"[ML] WARN scorer load failed: {self.load_error}", flush=True)

        if self.scorer is not None:
            try:
                import shap
                self.shap_explainer = shap.TreeExplainer(self.scorer.model)
                print(f"[ML] SHAP TreeExplainer bound (shap {shap.__version__})", flush=True)
            except Exception as e:
                self.shap_error = f"{type(e).__name__}: {str(e)}"
                print(f"[ML] WARN SHAP unavailable: {self.shap_error}", flush=True)

        self._loaded = True

    def is_ready(self) -> bool:
        return self.scorer is not None and self._extract is not None

    def status(self) -> Dict[str, Any]:
        return {
            "ready": self.is_ready(),
            "load_error": self.load_error,
            "model_version": self.scorer.version if self.scorer else None,
            "feature_count": len(self.scorer.features) if self.scorer else 0,
            "coverage_threshold": self.scorer.coverage_threshold if self.scorer else None,
            "thresholds": {"red": self.scorer.red_thr, "yellow": self.scorer.yel_thr}
                          if self.scorer else None,
            "shap_ready": self.shap_explainer is not None,
            "shap_error": self.shap_error,
            "sources": sorted(self._mappers.keys()),
        }

    def _shap_top(self, feat: Dict[str, Any], top_n: int) -> Optional[Dict[str, Any]]:
        if self.shap_explainer is None:
            return None
        try:
            X = self.scorer._preprocess(feat)
            sv = self.shap_explainer.shap_values(X)
            if isinstance(sv, list):
                sv = sv[1] if len(sv) > 1 else sv[0]
            sv = np.asarray(sv)
            if sv.ndim == 3:
                sv = sv[..., 1]
            sv = sv.reshape(-1)
            base = float(np.asarray(self.shap_explainer.expected_value).reshape(-1)[-1])
            order = np.argsort(np.abs(sv))[::-1][:top_n]
            top = []
            for i in order:
                fname = self.scorer.features[int(i)]
                v = feat.get(fname)
                if isinstance(v, (int, float)) and v == v and not np.isinf(float(v)):
                    v = round(float(v), 6)
                else:
                    v = None
                top.append({
                    "feature": fname,
                    "contribution": round(float(sv[i]), 6),
                    "direction": "increases_risk" if sv[i] > 0 else "decreases_risk",
                    "value": v,
                })
            return {"method": "SHAP TreeExplainer (margin/log-odds space)",
                    "base_value": round(base, 6), "top_features": top}
        except Exception as e:
            return {"error": f"{type(e).__name__}: {str(e)}"}

    def score_canonical(self, cdms: List, top_n: int = 5) -> Dict[str, Any]:
        """Score a conjunction given its CanonicalCDM history (already mapped)."""
        if not self.is_ready():
            return {"tier": None, "error": "ML service not loaded", "load_error": self.load_error}
        if not cdms:
            return {"tier": "UNAVAILABLE", "ml_score": None, "reason": "no CDMs provided",
                    "model_version": self.scorer.version}
        feat = self._extract(cdms)
        res = self.scorer.score_features(feat)
        out = {
            "tier": res.get("tier"),
            "ml_score": res.get("ml_score"),
            "coverage": res.get("coverage"),
            "coverage_threshold": self.scorer.coverage_threshold,
            "model_version": res.get("version"),
            "thresholds": ({"red": res.get("red_threshold"), "yellow": res.get("yellow_threshold")}
                           if res.get("tier") != "UNAVAILABLE" else None),
        }
        if res.get("tier") == "UNAVAILABLE":
            out["reason"] = res.get("reason")
            out["top_missing_features"] = res.get("top_missing_features")
            out["explanation"] = None
        else:
            out["explanation"] = self._shap_top(feat, top_n)
        return out

    def score_raw(self, cdms: List[Dict[str, Any]], source: str = "spacetrack_public",
                  top_n: int = 5) -> Dict[str, Any]:
        """Map raw source CDM(s) -> CanonicalCDM -> score."""
        if not self.is_ready():
            return {"tier": None, "error": "ML service not loaded", "load_error": self.load_error}
        mapper_cls = self._mappers.get(source)
        if mapper_cls is None:
            return {"tier": None, "error": f"unknown source '{source}'",
                    "known_sources": sorted(self._mappers.keys())}
        try:
            mapper = mapper_cls()
            canon = [mapper.from_source(c) for c in cdms]
        except Exception as e:
            return {"tier": None, "error": f"mapping failed: {type(e).__name__}: {str(e)}"}
        out = self.score_canonical(canon, top_n=top_n)
        out["source"] = source
        return out


ml_service = MLInferenceService()
