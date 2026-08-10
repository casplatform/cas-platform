"""JWT authentication — cas_engine.py AuthManager ile UYUMLU.

Mevcut AuthManager:
- AUTH_SECRET env değişkeni
- HS256 algoritma
- Payload: {"uid": int, "email": str, "iat": int, "exp": int}
- Legacy 2-segment fallback — bu modülde DESTEKLENMİYOR (kullanıcılar standart JWT'ye geçmiş olmalı)
"""
from typing import Optional
import jwt as pyjwt
from fastapi import HTTPException, Header, status
from pydantic import BaseModel

from core.config import settings
from core.database import get_dict_cursor


class CurrentUser(BaseModel):
    id: int
    email: str
    role: str = "operator"
    tier: str = "free"


def decode_token(token: str) -> dict:
    """JWT decode, mevcut AuthManager.decode_token ile UYUMLU."""
    try:
        # AUTH_SECRET kullan (cas_engine.py'daki AuthManager.secret ile aynı)
        payload = pyjwt.decode(
            token,
            settings.auth_secret,
            algorithms=[settings.jwt_algorithm],
        )
        return payload
    except pyjwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token expired",
        )
    except pyjwt.InvalidTokenError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid token: {type(e).__name__}",
        )


def fetch_user(user_id: int) -> Optional[dict]:
    try:
        with get_dict_cursor() as cur:
            cur.execute(
                "SELECT id, email, role, tier, is_active FROM users WHERE id=%s",
                (user_id,)
            )
            row = cur.fetchone()
            if row and row.get("is_active"):
                return dict(row)
            return None
    except Exception:
        return None


async def get_current_user(
    authorization: Optional[str] = Header(default=None),
) -> CurrentUser:
    if not authorization:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authorization header required",
        )
    if not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Bearer token required",
        )
    token = authorization[7:].strip()
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Empty token",
        )

    payload = decode_token(token)
    user_id = payload.get("uid") or payload.get("user_id") or payload.get("id")
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token missing user id",
        )

    user_row = fetch_user(int(user_id))
    if not user_row:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found or inactive",
        )

    return CurrentUser(
        id=user_row["id"],
        email=user_row["email"],
        role=user_row.get("role", "operator"),
        tier=user_row.get("tier", "free"),
    )


# ============================================================
# Tier feature-access enforcement (added 2026-07-08)
# ============================================================
from fastapi import HTTPException, Depends
from core.tier_features import has_feature, min_tier_for, tier_name


def require_feature(feature: str):
    """FastAPI dependency factory: gate an endpoint behind a tier feature.

    Usage:
        @router.post("/score", dependencies=[Depends(require_feature("ml_access"))])
    or, to also receive the user object:
        user: CurrentUser = Depends(require_feature("ml_access"))

    Returns 403 with an upgrade hint if the user's tier lacks the feature.
    Auth (get_current_user) runs first, so unauthenticated -> 401 automatically.
    """
    def _gate(user: "CurrentUser" = Depends(get_current_user)) -> "CurrentUser":
        if not has_feature(user.tier, feature):
            raise HTTPException(
                status_code=403,
                detail={
                    "error": "tier_upgrade_required",
                    "feature": feature,
                    "current_tier": tier_name(user.tier),
                    "required_tier": min_tier_for(feature),
                    "message": f"This feature requires the {min_tier_for(feature)} plan or higher.",
                },
            )
        return user
    return _gate


def require_reporting(level: str):
    """Dependency factory for graded reporting access (monthly < full).

    Usage:
        user: CurrentUser = Depends(require_reporting("monthly"))   # Starter+
        user: CurrentUser = Depends(require_reporting("full"))      # Pro+
    """
    from core.tier_features import has_reporting, min_tier_for_reporting

    def _gate(user: "CurrentUser" = Depends(get_current_user)) -> "CurrentUser":
        if not has_reporting(user.tier, level):
            raise HTTPException(
                status_code=403,
                detail={
                    "error": "tier_upgrade_required",
                    "feature": f"reporting:{level}",
                    "current_tier": tier_name(user.tier),
                    "required_tier": min_tier_for_reporting(level),
                    "message": f"This report requires the {min_tier_for_reporting(level)} plan or higher.",
                },
            )
        return user
    return _gate
