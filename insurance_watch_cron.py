#!/usr/bin/env python3
"""Weekly threshold check for insured orbits.

Sweeps every active watch, and emails an underwriter only when something has
materially changed. Silence is the normal outcome — an alert that fires every
week is an alert nobody reads.

Cron:  0 7 * * 1  cd /opt/cas && python3 insurance_watch_cron.py
"""
import os
import smtplib
import sys
from datetime import datetime, timezone
from email.mime.text import MIMEText
from email.utils import formataddr

for line in open("/opt/cas/.env"):
    if "=" in line and not line.strip().startswith("#"):
        k, v = line.strip().split("=", 1)
        os.environ.setdefault(k, v.strip().strip('"'))

sys.path.insert(0, "/opt/cas/cas_api")
sys.path.insert(0, "/opt/cas")

from core.database import init_pool, get_dict_cursor  # noqa: E402
from services import insurance_watch as W             # noqa: E402

SMTP_HOST = os.environ.get("SMTP_HOST", "mail.privateemail.com")
SMTP_PORT = int(os.environ.get("SMTP_PORT") or "587")
SMTP_USER = os.environ.get("SMTP_USER", "")
SMTP_PASS = os.environ.get("SMTP_PASS", "")
FROM_ADDR = os.environ.get("SMTP_FROM", SMTP_USER or "noreply@casplatform.com")


def log(msg):
    print(f"[{datetime.now(timezone.utc):%Y-%m-%d %H:%M:%S}] {msg}", flush=True)


def _email_for(user_id: int):
    try:
        with get_dict_cursor() as cur:
            cur.execute("SELECT email, name FROM users WHERE id=%s AND is_active=true",
                        (user_id,))
            r = cur.fetchone()
            return (r["email"], r.get("name")) if r else (None, None)
    except Exception as e:
        log(f"  email lookup failed for user {user_id}: {e}")
        return (None, None)


def _describe(trig: dict) -> str:
    t = trig.get("type")
    if t == "threat_growth":
        return (f"Threat population grew {trig['delta_pct']}% "
                f"({int(trig['old'])} \u2192 {int(trig['new'])} non-manoeuvrable objects)")
    if t == "percentile":
        return (f"LEO percentile climbed {trig['delta_points']} points "
                f"({trig['old']} \u2192 {trig['new']})")
    if t == "fragmentation":
        n = trig.get("count", 0)
        names = ", ".join(
            (e.get("parent1_object_name") or f"NORAD {e.get('parent1_norad_id')}")
            for e in (trig.get("events") or [])[:2])
        return (f"{n} fragmentation event{'s' if n != 1 else ''} recorded in this band"
                + (f" ({names})" if names else ""))
    return str(t)


def _body(items) -> str:
    lines = ["Orbits you monitor have changed materially since the last check.", ""]
    for it in items:
        inc = it.get("inclination_deg")
        orbit = f"{it['altitude_km']:.0f} km" + (f" / {inc:.1f}\u00b0" if inc is not None else " / all inclinations")
        lines.append(f"{it['watch']}  \u2014  {orbit}")
        for t in it["triggers"]:
            lines.append(f"  \u2022 {_describe(t)}")
        lines.append("")
    lines += [
        "Sign in to generate an updated Orbital Risk Factor Sheet:",
        "https://www.casplatform.com/insurance.html",
        "",
        "\u2014",
        "This is an environment-change notification, not a claim, a premium",
        "recommendation or a collision warning. CAS reports orbital exposure;",
        "underwriting decisions remain yours.",
        "",
        "CAS Platform \u00b7 casplatform.com",
    ]
    return "\n".join(lines)


def send(to_addr: str, items) -> bool:
    n = sum(len(i["triggers"]) for i in items)
    subj = f"CAS Orbital Risk Alert \u2014 {len(items)} orbit(s), {n} change(s)"
    msg = MIMEText(_body(items), "plain", "utf-8")
    msg["Subject"] = subj
    msg["From"] = formataddr(("CAS Platform", FROM_ADDR))
    msg["To"] = to_addr
    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=20) as s:
            s.starttls()
            if SMTP_USER and SMTP_PASS:
                s.login(SMTP_USER, SMTP_PASS)
            s.sendmail(FROM_ADDR, [to_addr], msg.as_string())
        return True
    except Exception as e:
        log(f"  SMTP failed for {to_addr}: {e}")
        return False


def main():
    try:
        init_pool()
    except Exception as e:
        log(f"pool init: {e}")

    res = W.check_all()
    log(f"checked {res['checked']} watches, {res['fired']} trigger(s) fired")

    if not res["users_to_notify"]:
        log("nothing to notify")
        return

    for user_id, items in res["users_to_notify"].items():
        addr, name = _email_for(user_id)
        if not addr:
            log(f"  user {user_id}: no active email, skipped")
            continue
        ok = send(addr, items)
        log(f"  user {user_id} ({addr}): {'sent' if ok else 'FAILED'} "
            f"\u2014 {len(items)} orbit(s)")
        if ok:
            try:
                with get_dict_cursor() as cur:
                    cur.execute("""UPDATE insurance_watch_events SET notified=true
                                   WHERE user_id=%s AND notified=false""", (user_id,))
            except Exception as e:
                log(f"  could not mark notified: {e}")


if __name__ == "__main__":
    main()
