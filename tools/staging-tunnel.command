#!/usr/bin/env bash
#
# CAS staging tunnel — double-click this file in Finder.
#
# Staging listens on 127.0.0.1 only: no DNS name, no certificate, nothing for a
# crawler to find and nothing exposed if a firewall rule is ever wrong. The
# trade-off is that reaching it needs a tunnel. This forwards the staging nginx
# port to the same port locally, so the browser talks to it as if it were here.
#
# Leave this window open while you work. Close it, or press Ctrl-C, to
# disconnect.
#
# Copy to the Mac (once):
#   scp root@213.199.57.173:/opt/cas/tools/staging-tunnel.command ~/Desktop/
#   chmod +x ~/Desktop/staging-tunnel.command

SERVER="root@213.199.57.173"
PORT=8080

cd "$(dirname "$0")" || exit 1

printf '\033[36mCAS staging tunnel\033[0m\n'
printf '  server : %s\n' "$SERVER"
printf '  open   : \033[4mhttp://localhost:%s/portal.html\033[0m\n\n' "$PORT"

if lsof -Pi :$PORT -sTCP:LISTEN -t >/dev/null 2>&1; then
  printf '\033[31mPort %s is already in use locally.\033[0m\n' "$PORT"
  printf 'Another tunnel is probably already running -- try the URL above first.\n'
  read -r -p 'Press Return to close. '
  exit 1
fi

printf 'Connecting... (this window stays open; Ctrl-C to disconnect)\n\n'

# -N: no remote command, just forwarding.
# ExitOnForwardFailure: fail loudly rather than opening a tunnel that forwards
# nothing, which looks identical to a broken staging service from the browser.
# ServerAlive*: drop a dead connection in ~1 min instead of hanging.
ssh -N \
    -L "${PORT}:127.0.0.1:${PORT}" \
    -o ExitOnForwardFailure=yes \
    -o ServerAliveInterval=20 \
    -o ServerAliveCountMax=3 \
    "$SERVER"

printf '\n\033[33mTunnel closed.\033[0m\n'
read -r -p 'Press Return to close this window. '
