"""ML scoring & explainability endpoints — /api/v2/ml/*

- POST /api/v2/ml/score   raw CDM(s) -> canonical -> G1 gate -> tier (+SHAP)
- GET  /api/v2/ml/status  model / gate / SHAP readiness

Scorer is production-honest (canonical-v1, 107 features, no mission_id) and
gated: sparse CDMs (e.g. Space-Track public) return tier=UNAVAILABLE, deferring
to the deterministic Pc funnel.

Auth: /status requires authentication; /score requires the Pro plan or higher
(tier feature: ml_access). Enforced via core.auth.require_feature.
"""
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel, Field

from core.auth import CurrentUser, get_current_user, require_feature
from services.ml_inference import ml_service

router = APIRouter(prefix="/ml", tags=["ml"])


class CDMScoreRequest(BaseModel):
    """Raw source CDM(s) for one conjunction. Provide `cdm` (single) or `cdms`
    (history list). Extra fields accepted so full CDM payloads pass through."""
    model_config = {"extra": "allow"}
    cdm: Optional[Dict[str, Any]] = Field(None, description="Single raw CDM dict")
    cdms: Optional[List[Dict[str, Any]]] = Field(None, description="CDM history (list)")
    source: str = Field("spacetrack_public", description="Source mapper key")
    top_n_features: int = Field(5, ge=1, le=20)


@router.get("/status")
async def ml_status(user: CurrentUser = Depends(get_current_user)):
    """ML service readiness. Any authenticated user may see it (upgrade signal)."""
    return ml_service.status()


@router.post("/score")
async def ml_score(
    req: CDMScoreRequest,
    user: CurrentUser = Depends(require_feature("ml_access")),
):
    if not ml_service.is_ready():
        raise HTTPException(status_code=503,
                            detail=f"ML service not loaded: {ml_service.load_error}")
    cdms = req.cdms if req.cdms else ([req.cdm] if req.cdm else None)
    if not cdms:
        raise HTTPException(status_code=400, detail="provide 'cdm' (single) or 'cdms' (list)")
    return ml_service.score_raw(cdms, source=req.source, top_n=req.top_n_features)
