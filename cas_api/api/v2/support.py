"""Support ticket endpoints — /api/v2/support/*

In-app support and bug reporting for both portals (operator and insurer).
Authenticated users file a ticket with an optional screenshot; session context
(page, catalogue source, user agent) is attached automatically to help triage.

Not a public contact form — that lives on the landing page. This is for
signed-in users reporting a problem or requesting a change from inside a portal.
"""
import json
import logging
import time
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from core.auth import get_current_user, CurrentUser
from core.database import get_dict_cursor

log = logging.getLogger(__name__)
router = APIRouter(tags=["support"])

# Base64 of a 2 MB image is ~2.75 MB of text. Cap the decoded size at 2 MB by
# capping the string a little above that; reject anything larger outright.
_MAX_SHOT_CHARS = 2_800_000
_ALLOWED_PREFIXES = ("data:image/png;base64,", "data:image/jpeg;base64,")
_RATE_PER_HOUR = 5


class TicketIn(BaseModel):
    category: str = Field(..., max_length=32)
    subject: str = Field(..., min_length=1, max_length=200)
    body: str = Field(..., min_length=1, max_length=5000)
    portal: Optional[str] = Field(None, max_length=16)
    screenshot: Optional[str] = None
    context: Optional[Dict[str, Any]] = None


@router.post("/support/ticket")
def create_ticket(payload: TicketIn,
                  user: CurrentUser = Depends(get_current_user)) -> Dict[str, Any]:
    cat = payload.category.strip().lower()
    if cat not in ("bug", "data", "feature", "other"):
        cat = "other"

    shot = payload.screenshot
    if shot:
        if not shot.startswith(_ALLOWED_PREFIXES):
            raise HTTPException(status_code=400,
                                detail="screenshot must be a PNG or JPEG data URI")
        if len(shot) > _MAX_SHOT_CHARS:
            raise HTTPException(status_code=413,
                                detail="screenshot exceeds the 2 MB limit")

    portal = (payload.portal or "").strip().lower()
    if portal not in ("operator", "insurer"):
        portal = "insurer" if user.role == "insurer" else "operator"

    ctx = payload.context if isinstance(payload.context, dict) else {}

    with get_dict_cursor() as cur:
        # Rate limit: at most _RATE_PER_HOUR tickets per user per hour.
        cur.execute(
            "SELECT count(*) AS n FROM support_tickets "
            "WHERE user_id = %s AND created_at > now() - interval '1 hour'",
            (user.id,),
        )
        if cur.fetchone()["n"] >= _RATE_PER_HOUR:
            raise HTTPException(status_code=429,
                                detail="too many tickets in the last hour; please try later")

        cur.execute(
            "INSERT INTO support_tickets "
            "(user_id, email, role, portal, category, subject, body, screenshot, context) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
            (user.id, user.email, user.role, portal, cat,
             payload.subject.strip(), payload.body.strip(), shot,
             json.dumps(ctx)),
        )
        ticket_id = cur.fetchone()["id"]

    log.info("support ticket #%s from user %s (%s/%s)",
             ticket_id, user.id, portal, cat)
    return {"ok": True, "ticket_id": ticket_id}
