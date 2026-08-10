#!/usr/bin/env python3
import smtplib, sys
from email.mime.text import MIMEText

status = sys.argv[1] if len(sys.argv) > 1 else "UNKNOWN"
timestamp = sys.argv[2] if len(sys.argv) > 2 else "?"

env = {}
for line in open("/opt/cas/.env"):
    if "=" in line and not line.startswith("#"):
        k, v = line.strip().split("=", 1)
        env[k] = v

smtp_user = env.get("SMTP_USER", "")
smtp_pass = env.get("SMTP_PASS", "")
if not smtp_user or not smtp_pass:
    sys.exit(0)

body = f"""CAS Engine Alert

Status: {status}
Time: {timestamp}
Server: casplatform.com

The CAS engine was found unresponsive.
Auto-restart was attempted.

Check: ssh root@213.199.57.173
Logs: journalctl -u cas -n 50
"""

msg = MIMEText(body)
msg["Subject"] = f"CAS ENGINE: {status}"
msg["From"] = smtp_user
msg["To"] = smtp_user

try:
    server = smtplib.SMTP_SSL(env.get("SMTP_HOST", "mail.privateemail.com"), 465)
    server.login(smtp_user, smtp_pass)
    server.send_message(msg)
    server.quit()
    print(f"Alert email sent: {status}")
except Exception as e:
    print(f"Email failed: {e}")
