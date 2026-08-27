#!/bin/bash
#
# CAS engine watchdog. Real crontab (verified 2026-08-27, root's crontab):
#     */5 * * * * /opt/cas/watchdog.sh
#
# Production only, which is why :8765 and /opt/cas are written literally here:
# the single caller is that absolute path. Staging carries a copy of this file
# because the whole tree is copied, and nothing invokes it there.
HEALTH=$(curl -s --max-time 30 http://localhost:8765/health 2>/dev/null)
if echo "$HEALTH" | grep -q '"ok"'; then
  exit 0
fi

TIMESTAMP=$(date '+%Y-%m-%d %H:%M:%S')
echo "[$TIMESTAMP] ENGINE DOWN - restarting" >> /var/log/cas_watchdog.log

# stop -> sleep -> start, never `systemctl restart`. The engine binds 8765 with
# allow_reuse_address and a restart races its own shutdown: the new process can
# reach bind() before the old one has released the port, and the unit then fails
# with the watchdog having reported a restart it did not achieve. This is the
# sequence CLAUDE.md and scripts/deploy.sh both use, for the same reason.
systemctl stop cas
sleep 3
systemctl start cas

# Poll instead of a fixed sleep. The engine needs ~10s from start to answering,
# so the old `sleep 5` checked while it was still coming up and could file a
# CRITICAL for a service that recovered two seconds later -- a false alarm from
# the one script whose whole job is telling the truth about whether it is up.
HEALTH2=""
for _ in $(seq 1 20); do
  HEALTH2=$(curl -s --max-time 5 http://localhost:8765/health 2>/dev/null)
  echo "$HEALTH2" | grep -q '"ok"' && break
  sleep 2
done

if echo "$HEALTH2" | grep -q '"ok"'; then
  echo "[$TIMESTAMP] RECOVERED" >> /var/log/cas_watchdog.log
  /opt/cas/watchdog_notify.py "RECOVERED" "$TIMESTAMP" &
else
  echo "[$TIMESTAMP] STILL DOWN" >> /var/log/cas_watchdog.log
  /opt/cas/watchdog_notify.py "CRITICAL" "$TIMESTAMP" &
fi
