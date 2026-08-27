"""JWT authentication — cas_engine.py AuthManager ile UYUMLU.

Mevcut AuthManager:
- AUTH_SECRET env değişkeni
- HS256 algoritma
- Payload: {"uid": int, "email": str, "iat": int, "exp": int}
- Legacy 2-segment fallback — bu modülde DESTEKLENMİYOR (kullanıcılar standart JWT'ye geçmiş olmalı)
"""
import logging
from typing import Optional
import jwt as pyjwt
import psycopg2
from fastapi import HTTPException, Header, status
from pydantic import BaseModel

from core.config import settings
from core.database import get_dict_cursor

log = logging.getLogger("cas.auth")


class AuthBackendUnavailable(Exception):
    """The credential could not be checked, as opposed to being found invalid.

    These are different facts and they were being reported as the same one.
    fetch_user() caught every exception and returned None, and None is also
    what "no such user" looks like -- so a database outage arrived at the
    client as 401 "User not found or inactive", for every user at once, with
    nothing written to any log. The outage was indistinguishable from a
    password problem, both to the person holding the token and to us.
    """


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
    """The user row, or None if there genuinely is not one.

    Raises AuthBackendUnavailable when the answer is unknown. None now means
    exactly one thing -- no such active user -- so the caller can act on it.
    """
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
    except (psycopg2.OperationalError, psycopg2.InterfaceError, RuntimeError) as e:
        # Connection-level: the pool could not hand out a usable connection, or
        # was never initialised. Nothing here says anything about this user.
        log.error("auth: user lookup unavailable for uid=%s: %s: %s",
                  user_id, type(e).__name__, e)
        raise AuthBackendUnavailable(str(e)) from e
    except Exception as e:
        # Anything else is ours -- a bad query, a schema drift. Fail closed,
        # but never silently: a 401 with no log line is what made the last
        # outage invisible.
        log.exception("auth: user lookup failed for uid=%s: %s", user_id, type(e).__name__)
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

    # 503, not 401, and the reasoning is worth keeping. 401 is a statement
    # about the caller: your credential is no good. During an outage that
    # statement is false, and it is acted on -- a browser drops the token and
    # sends the user to a login that also fails, an API client treats 401 as
    # permanent and stops retrying. 503 is a statement about us, it is true,
    # and it is the one clients already know how to retry.
    #
    # What the body says is deliberately thin. It reveals only that the service
    # is degraded, which any endpoint already shows, and it is identical whether
    # the user exists or not -- we could not look them up, so there is nothing
    # user-specific to leak even by accident. The exception text goes to the log,
    # where the operator needs it and the caller cannot see it.
    try:
        user_row = fetch_user(int(user_id))
    except AuthBackendUnavailable:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Authentication is temporarily unavailable. Please retry shortly.",
            headers={"Retry-After": "30"},
        )
    if not user_row:
        log.info("auth: rejected uid=%s -- no active user row", user_id)
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
