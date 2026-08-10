"""Notification endpoints — /api/v2/notifications/*

3 endpoint:
- GET  /prefs   → kullanıcının ayarları
- PUT  /prefs   → güncelle (Pydantic validation otomatik)
- POST /test    → kanal test et
"""
import time
from fastapi import APIRouter, Depends, HTTPException, status

from core.auth import get_current_user, CurrentUser
from core.database import get_dict_cursor
from schemas.notifications import (
    NotificationPrefsResponse,
    NotificationPrefsUpdate,
    NotificationTestRequest,
    NotificationTestResponse,
    ConjunctionPayload,
)
from services.webhooks import WebhookSender


router = APIRouter(prefix="/notifications", tags=["notifications"])


@router.get("/prefs", response_model=NotificationPrefsResponse)
async def get_prefs(user: CurrentUser = Depends(get_current_user)) -> NotificationPrefsResponse:
    """Kullanıcının notification ayarlarını döndür.

    Kayıt yoksa default'lar dönülür (email=True, min_risk=RED, diğerleri boş).
    """
    try:
        with get_dict_cursor() as cur:
            cur.execute(
                """SELECT alert_email, min_risk, slack_url, teams_url, webhook_url,
                          (webhook_secret IS NOT NULL) as webhook_secret_set
                   FROM notification_prefs WHERE user_id=%s""",
                (user.id,)
            )
            row = cur.fetchone()
            if row:
                return NotificationPrefsResponse(
                    alert_email=row["alert_email"] if row["alert_email"] is not None else True,
                    min_risk=row["min_risk"] or "RED",
                    slack_url=row["slack_url"] or "",
                    teams_url=row["teams_url"] or "",
                    webhook_url=row["webhook_url"] or "",
                    webhook_secret_set=bool(row["webhook_secret_set"]),
                )
    except Exception as e:
        # Default'a fall-back
        pass
    return NotificationPrefsResponse()


@router.put("/prefs", response_model=NotificationPrefsResponse)
async def update_prefs(
    update: NotificationPrefsUpdate,
    user: CurrentUser = Depends(get_current_user),
) -> NotificationPrefsResponse:
    """Notification ayarlarını güncelle.

    Pydantic validation OTOMATİK (URL schema, host whitelist, vb.).
    Geçersiz field → 422 Unprocessable Entity (detaylı hata mesajı).

    Sadece gönderilen field'lar güncellenir (PATCH benzeri davranış).
    Boş string ("") → kolonu NULL yap (kanalı kapat).
    """
    try:
        with get_dict_cursor() as cur:
            # Önce mevcut kaydı al (varsa)
            cur.execute(
                """SELECT alert_email, min_risk, slack_url, teams_url, webhook_url, webhook_secret
                   FROM notification_prefs WHERE user_id=%s""",
                (user.id,)
            )
            current = cur.fetchone()

            # Default veya mevcut değerlerle başla
            new_values = {
                "alert_email": current["alert_email"] if current else True,
                "min_risk": current["min_risk"] if current else "RED",
                "slack_url": current["slack_url"] if current else None,
                "teams_url": current["teams_url"] if current else None,
                "webhook_url": current["webhook_url"] if current else None,
                "webhook_secret": current["webhook_secret"] if current else None,
            }

            # Update body'sinde gelen field'ları uygula
            if update.alert_email is not None:
                new_values["alert_email"] = update.alert_email
            if update.min_risk is not None:
                new_values["min_risk"] = update.min_risk
            if update.slack_url is not None:
                new_values["slack_url"] = update.slack_url or None
            if update.teams_url is not None:
                new_values["teams_url"] = update.teams_url or None
            if update.webhook_url is not None:
                new_values["webhook_url"] = update.webhook_url or None
            if update.webhook_secret is not None:
                new_values["webhook_secret"] = update.webhook_secret or None

            # UPSERT
            cur.execute(
                """INSERT INTO notification_prefs
                   (user_id, alert_email, min_risk, slack_url, teams_url, webhook_url, webhook_secret)
                   VALUES (%s, %s, %s, %s, %s, %s, %s)
                   ON CONFLICT (user_id) DO UPDATE SET
                       alert_email=EXCLUDED.alert_email,
                       min_risk=EXCLUDED.min_risk,
                       slack_url=EXCLUDED.slack_url,
                       teams_url=EXCLUDED.teams_url,
                       webhook_url=EXCLUDED.webhook_url,
                       webhook_secret=EXCLUDED.webhook_secret""",
                (
                    user.id,
                    new_values["alert_email"],
                    new_values["min_risk"],
                    new_values["slack_url"],
                    new_values["teams_url"],
                    new_values["webhook_url"],
                    new_values["webhook_secret"],
                )
            )

            return NotificationPrefsResponse(
                alert_email=new_values["alert_email"],
                min_risk=new_values["min_risk"],
                slack_url=new_values["slack_url"] or "",
                teams_url=new_values["teams_url"] or "",
                webhook_url=new_values["webhook_url"] or "",
                webhook_secret_set=bool(new_values["webhook_secret"]),
            )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"db: {type(e).__name__}: {e}",
        )


@router.post("/test", response_model=NotificationTestResponse)
async def test_channel(
    request: NotificationTestRequest,
    user: CurrentUser = Depends(get_current_user),
) -> NotificationTestResponse:
    """Bir kanalı test et — kayıtlı URL ile sahte conjunction gönder.

    Kullanıcının önce ilgili kanal URL'ini kaydetmiş olması gerek.
    Gönderim başarısız olursa 502 dönülür.
    """
    # Kullanıcının URL'ini al
    try:
        with get_dict_cursor() as cur:
            cur.execute(
                "SELECT slack_url, teams_url, webhook_url, webhook_secret FROM notification_prefs WHERE user_id=%s",
                (user.id,)
            )
            row = cur.fetchone()
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"db: {e}",
        )

    if not row:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Notification preferences not configured. Save preferences first."
        )

    # Sahte conjunction payload (test verisi)
    test_conj = ConjunctionPayload(
        risk="RED",
        sat1="TEST-SAT-1",
        sat2="TEST-DEBRIS-A",
        norad1=99999,
        norad2=99998,
        miss_distance_m=42,
        pc=0.001,
        pc_str="1 in 1000",
        tca_str="2026-05-31T12:00:00Z",
        cdm_id=f"TEST-CDM-{int(time.time())}",
    )

    # Kanala göre gönder
    if request.channel == "slack":
        if not row["slack_url"]:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="slack_url not configured"
            )
        ok, detail = await WebhookSender.send_slack(row["slack_url"], test_conj)
    elif request.channel == "teams":
        if not row["teams_url"]:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="teams_url not configured"
            )
        ok, detail = await WebhookSender.send_teams(row["teams_url"], test_conj)
    else:  # webhook
        if not row["webhook_url"]:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="webhook_url not configured"
            )
        ok, detail = await WebhookSender.send_generic(
            row["webhook_url"], test_conj, secret=row["webhook_secret"]
        )

    response = NotificationTestResponse(
        status="ok" if ok else "fail",
        channel=request.channel,
        detail=detail,
    )

    if not ok:
        # 502 Bad Gateway — upstream webhook başarısız
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=response.model_dump(),
        )

    return response
