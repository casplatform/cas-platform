#!/bin/bash
HEALTH=$(curl -s --max-time 30 http://localhost:8765/health 2>/dev/null)
if echo "$HEALTH" | grep -q '"ok"'; then
  exit 0
fi

TIMESTAMP=$(date '+%Y-%m-%d %H:%M:%S')
echo "[$TIMESTAMP] ENGINE DOWN - restarting" >> /var/log/cas_watchdog.log

systemctl restart cas
sleep 5

HEALTH2=$(curl -s --max-time 30 http://localhost:8765/health 2>/dev/null)
if echo "$HEALTH2" | grep -q '"ok"'; then
  echo "[$TIMESTAMP] RECOVERED" >> /var/log/cas_watchdog.log
  /opt/cas/watchdog_notify.py "RECOVERED" "$TIMESTAMP" &
else
  echo "[$TIMESTAMP] STILL DOWN" >> /var/log/cas_watchdog.log
  /opt/cas/watchdog_notify.py "CRITICAL" "$TIMESTAMP" &
fi
