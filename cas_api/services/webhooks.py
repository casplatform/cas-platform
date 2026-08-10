"""Webhook gönderici — Slack/Teams/Generic.

Tasarım kararları:
- httpx ile async POST (FastAPI native)
- 5sn timeout
- Hata logging-only, exception fırlatmaz (caller bilse de çağırma)
- HMAC-SHA256 imza generic webhook için (opsiyonel)

Kullanım:
    sender = WebhookSender()
    ok, detail = await sender.send_slack(url, payload)
"""
import hmac
import hashlib
import json
import time
from typing import Optional, Tuple

import httpx

from schemas.notifications import ConjunctionPayload


class WebhookSender:
    """Slack, MS Teams ve Generic webhook gönderici."""

    TIMEOUT = 5.0
    USER_AGENT = "CAS-Platform/0.1 (casplatform.com)"

    @staticmethod
    def _build_slack_payload(conj: ConjunctionPayload) -> dict:
        """Slack Block Kit formatı."""
        risk = conj.risk
        color = "#e74c3c" if risk == "RED" else "#f39c12"
        return {
            "text": f"CAS {risk} ALERT: {conj.sat1} ↔ {conj.sat2}",
            "attachments": [{
                "color": color,
                "blocks": [
                    {
                        "type": "header",
                        "text": {"type": "plain_text", "text": f"⚠ {risk} CONJUNCTION"}
                    },
                    {
                        "type": "section",
                        "fields": [
                            {"type": "mrkdwn", "text": f"*Satellite 1*\n{conj.sat1}"},
                            {"type": "mrkdwn", "text": f"*Satellite 2*\n{conj.sat2}"},
                            {"type": "mrkdwn", "text": f"*Miss Distance*\n{conj.miss_distance_m} m"},
                            {"type": "mrkdwn", "text": f"*Pc*\n{conj.pc_str or conj.pc or '?'}"},
                            {"type": "mrkdwn", "text": f"*TCA*\n{conj.tca_str or '?'}"},
                            {"type": "mrkdwn", "text": f"*CDM ID*\n{conj.cdm_id or '?'}"},
                        ]
                    },
                    {
                        "type": "actions",
                        "elements": [{
                            "type": "button",
                            "text": {"type": "plain_text", "text": "Open Dashboard"},
                            "url": "https://www.casplatform.com/portal.html",
                            "style": "primary"
                        }]
                    }
                ]
            }]
        }

    @staticmethod
    def _build_teams_payload(conj: ConjunctionPayload) -> dict:
        """MS Teams Adaptive Card formatı."""
        risk = conj.risk
        color = "FF0000" if risk == "RED" else "FFA500"
        return {
            "@type": "MessageCard",
            "@context": "https://schema.org/extensions",
            "themeColor": color,
            "summary": f"CAS {risk}: {conj.sat1} ↔ {conj.sat2}",
            "title": f"⚠ {risk} CONJUNCTION ALERT",
            "sections": [{
                "facts": [
                    {"name": "Satellite 1:", "value": conj.sat1},
                    {"name": "Satellite 2:", "value": conj.sat2},
                    {"name": "Miss Distance:", "value": f"{conj.miss_distance_m} m"},
                    {"name": "Pc:", "value": str(conj.pc_str or conj.pc or "?")},
                    {"name": "TCA:", "value": str(conj.tca_str or "?")},
                    {"name": "CDM ID:", "value": str(conj.cdm_id or "?")},
                ],
                "markdown": True
            }],
            "potentialAction": [{
                "@type": "OpenUri",
                "name": "Open Dashboard",
                "targets": [{"os": "default", "uri": "https://www.casplatform.com/portal.html"}]
            }]
        }

    @staticmethod
    def _build_generic_payload(conj: ConjunctionPayload) -> dict:
        """Generic webhook — zengin JSON, machine-readable."""
        return {
            "event": "conjunction_alert",
            "version": "1.0",
            "severity": conj.risk,
            "satellite_1": conj.sat1,
            "satellite_2": conj.sat2,
            "norad_1": conj.norad1,
            "norad_2": conj.norad2,
            "miss_distance_m": conj.miss_distance_m,
            "pc": conj.pc,
            "pc_str": conj.pc_str,
            "tca": conj.tca_str,
            "cdm_id": conj.cdm_id,
            "portal_url": "https://www.casplatform.com/portal.html",
            "timestamp": int(time.time()),
        }

    @classmethod
    async def _post_json(
        cls,
        url: str,
        payload: dict,
        secret: Optional[str] = None,
    ) -> Tuple[bool, str]:
        """HTTPS POST JSON. Returns (ok, detail_string)."""
        data = json.dumps(payload).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "User-Agent": cls.USER_AGENT,
        }
        if secret:
            sig = hmac.new(
                secret.encode("utf-8"),
                data,
                hashlib.sha256,
            ).hexdigest()
            headers["X-CAS-Signature"] = f"sha256={sig}"

        try:
            async with httpx.AsyncClient(timeout=cls.TIMEOUT) as client:
                resp = await client.post(url, content=data, headers=headers)
                body_snippet = resp.text[:200] if resp.text else ""
                if 200 <= resp.status_code < 300:
                    return True, f"HTTP {resp.status_code} body={body_snippet}"
                return False, f"HTTP {resp.status_code} body={body_snippet}"
        except httpx.TimeoutException:
            return False, f"timeout (>{cls.TIMEOUT}s)"
        except httpx.RequestError as e:
            return False, f"request_error: {type(e).__name__}: {e}"
        except Exception as e:
            return False, f"unexpected: {type(e).__name__}: {e}"

    @classmethod
    async def send_slack(cls, url: str, conj: ConjunctionPayload) -> Tuple[bool, str]:
        return await cls._post_json(url, cls._build_slack_payload(conj))

    @classmethod
    async def send_teams(cls, url: str, conj: ConjunctionPayload) -> Tuple[bool, str]:
        return await cls._post_json(url, cls._build_teams_payload(conj))

    @classmethod
    async def send_generic(
        cls,
        url: str,
        conj: ConjunctionPayload,
        secret: Optional[str] = None,
    ) -> Tuple[bool, str]:
        return await cls._post_json(url, cls._build_generic_payload(conj), secret=secret)
