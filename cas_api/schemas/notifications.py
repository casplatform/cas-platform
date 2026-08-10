"""Notification preferences ve test request modelleri.

Pydantic v2 ile:
- HttpUrl: HTTPS validation otomatik
- field_validator: custom domain whitelist
- 422 yanıtları otomatik üretilir (FastAPI ile entegre)
"""
from typing import Optional, Literal
from urllib.parse import urlparse

from pydantic import BaseModel, Field, HttpUrl, field_validator


# Güvenlik whitelist'leri
SLACK_HOSTS = ("hooks.slack.com",)
TEAMS_HOSTS = (
    "webhook.office.com",
    "outlook.office.com",
    "outlook.office365.com",
)

# Private IP / metadata host yasak listesi
FORBIDDEN_HOST_PREFIXES = ("127.", "10.", "192.168.", "169.254.", "0.")
FORBIDDEN_HOSTS = (
    "localhost",
    "metadata.google.internal",
    "169.254.169.254",  # AWS/GCP metadata
)


def _validate_host_security(url: str) -> str:
    """SSRF koruması — private IP ve metadata host'ları banla."""
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if not host:
        raise ValueError("URL'de host yok")
    for prefix in FORBIDDEN_HOST_PREFIXES:
        if host.startswith(prefix):
            raise ValueError(f"Private IP yasak: {host}")
    if host in FORBIDDEN_HOSTS:
        raise ValueError(f"Internal host yasak: {host}")
    return host


# ── Notification Preferences (GET response, PUT request) ──

class NotificationPrefsResponse(BaseModel):
    """GET /api/v2/notifications/prefs response."""
    alert_email: bool = Field(default=True, description="Email bildirimleri açık mı")
    min_risk: Literal["RED", "YELLOW", "GREEN"] = Field(
        default="RED",
        description="Minimum alert seviyesi — bu seviye ve üstü gönderilir"
    )
    slack_url: str = Field(default="", description="Slack incoming webhook URL'i (boş = devre dışı)")
    teams_url: str = Field(default="", description="MS Teams incoming webhook URL'i (boş = devre dışı)")
    webhook_url: str = Field(default="", description="Generic webhook URL'i (boş = devre dışı)")
    webhook_secret_set: bool = Field(
        default=False,
        description="Generic webhook için HMAC secret tanımlı mı (değer dönmez, sadece flag)"
    )


class NotificationPrefsUpdate(BaseModel):
    """PUT /api/v2/notifications/prefs request.

    Tüm field'lar opsiyonel — sadece gönderilenler güncellenir.
    Boş string (""): kanalı sil (URL'i NULL yap).
    """
    alert_email: Optional[bool] = None
    min_risk: Optional[Literal["RED", "YELLOW", "GREEN"]] = None
    slack_url: Optional[str] = Field(
        default=None,
        max_length=500,
        description="Slack webhook URL'i. Boş string '' = sil. HTTPS + hooks.slack.com zorunlu."
    )
    teams_url: Optional[str] = Field(
        default=None,
        max_length=500,
        description="Teams webhook URL'i. Boş string '' = sil. HTTPS + Microsoft host zorunlu."
    )
    webhook_url: Optional[str] = Field(
        default=None,
        max_length=500,
        description="Generic webhook URL'i. Boş string '' = sil. HTTPS zorunlu."
    )
    webhook_secret: Optional[str] = Field(
        default=None,
        max_length=200,
        description="Generic webhook için HMAC-SHA256 secret. Boş string '' = sil."
    )

    @field_validator("slack_url")
    @classmethod
    def validate_slack_url(cls, v: Optional[str]) -> Optional[str]:
        if v is None or v == "":
            return v
        if not v.startswith("https://"):
            raise ValueError("HTTPS zorunlu")
        host = _validate_host_security(v)
        if not any(host == h or host.endswith("." + h) for h in SLACK_HOSTS):
            raise ValueError(f"Slack host olmalı: {SLACK_HOSTS}")
        return v

    @field_validator("teams_url")
    @classmethod
    def validate_teams_url(cls, v: Optional[str]) -> Optional[str]:
        if v is None or v == "":
            return v
        if not v.startswith("https://"):
            raise ValueError("HTTPS zorunlu")
        host = _validate_host_security(v)
        if not any(host == h or host.endswith("." + h) for h in TEAMS_HOSTS):
            raise ValueError(f"Microsoft Teams host olmalı: {TEAMS_HOSTS}")
        return v

    @field_validator("webhook_url")
    @classmethod
    def validate_webhook_url(cls, v: Optional[str]) -> Optional[str]:
        if v is None or v == "":
            return v
        if not v.startswith("https://"):
            raise ValueError("HTTPS zorunlu")
        _validate_host_security(v)
        return v


# ── Test endpoint ──

class NotificationTestRequest(BaseModel):
    """POST /api/v2/notifications/test request."""
    channel: Literal["slack", "teams", "webhook"] = Field(
        ...,
        description="Test edilecek kanal"
    )


class NotificationTestResponse(BaseModel):
    """POST /api/v2/notifications/test response."""
    status: Literal["ok", "fail"]
    channel: str
    detail: str = Field(description="Başarı/hata mesajı + HTTP yanıt özeti")


# ── Webhook ortak: conjunction payload (internal) ──

class ConjunctionPayload(BaseModel):
    """Webhook'lara gönderilen conjunction verisi (internal model)."""
    risk: str = "RED"
    sat1: str
    sat2: str
    norad1: Optional[int] = None
    norad2: Optional[int] = None
    miss_distance_m: float = 0.0
    pc: Optional[float] = None
    pc_str: Optional[str] = None
    tca_str: Optional[str] = None
    cdm_id: Optional[str] = None
