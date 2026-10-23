#!/bin/bash
# Cloudflare Tunnel in front of CTRANSLATE: a public HTTPS name (a real certificate from
# Cloudflare, so every phone trusts it and the microphone works), with no port opened
# on the router. cloudflared connects out to Cloudflare and forwards to CTRANSLATE's
# local-only listener on 127.0.0.1:8454.
#
#   sudo bash enable-tunnel.sh <tunnel-token> <public-name>
#   sudo bash enable-tunnel.sh --status
#   sudo bash enable-tunnel.sh --disable
#
# Before running, in the Cloudflare dashboard (Zero Trust -> Networks -> Tunnels):
#   1. Create a tunnel (type Cloudflared), name it e.g. ctranslate. Copy the token from
#      the install command it shows (the long string after --token / "service install").
#   2. Public Hostname -> Add: subdomain meet, your domain, Service HTTP, URL localhost:8454
#      (the same name you pass below).
# This script installs cloudflared from Cloudflare's own apt repository, runs it as a
# service with that token, and sets CTRANSLATE's public address to https://<public-name>
# so QR codes and join links use it.
set -euo pipefail
[ "$(id -u)" -eq 0 ] || { echo "run as root: sudo bash $0 ..." >&2; exit 1; }
DATA=/var/lib/ctranslate
say() { echo "== $*"; }

case "${1:-}" in
  --status)
    systemctl --no-pager status cloudflared 2>/dev/null | head -5 || echo "cloudflared is not installed"
    python3 -c "import json;print('public address:', json.load(open('$DATA/settings.json')).get('publicUrl') or '(not set)')" 2>/dev/null || true
    exit 0 ;;
  --disable)
    cloudflared service uninstall 2>/dev/null || systemctl disable --now cloudflared 2>/dev/null || true
    say "tunnel service removed (the tunnel itself stays in your Cloudflare account)"
    exit 0 ;;
  ""|-*) echo "usage: enable-tunnel.sh <tunnel-token> <public-name> | --status | --disable" >&2; exit 2 ;;
esac
TOKEN="$1"; NAME="${2:-}"
[ -n "$NAME" ] || { echo "usage: enable-tunnel.sh <tunnel-token> <public-name>   (e.g. meet.example.com)" >&2; exit 2; }
NAME=$(printf '%s' "$NAME" | tr '[:upper:]' '[:lower:]'); NAME=${NAME#https://}; NAME=${NAME%%/*}
printf '%s' "$NAME" | grep -Eqx '([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,}' || { echo "not a DNS name: $NAME" >&2; exit 2; }

DEB=$(ls /opt/ctranslate/pkgs/cloudflared*.deb 2>/dev/null | head -1 || true)
if ! command -v cloudflared >/dev/null && [ -n "$DEB" ]; then
  say "installing cloudflared from the install media ($(basename "$DEB"))"
  DEBIAN_FRONTEND=noninteractive apt-get install -y -qq "$DEB" >/dev/null || dpkg -i "$DEB" >/dev/null || true
fi
if ! command -v cloudflared >/dev/null; then
  say "installing cloudflared from pkg.cloudflare.com"
  install -d -m 0755 /usr/share/keyrings
  curl -fsSL https://pkg.cloudflare.com/cloudflare-main.gpg -o /usr/share/keyrings/cloudflare-main.gpg
  echo "deb [signed-by=/usr/share/keyrings/cloudflare-main.gpg] https://pkg.cloudflare.com/cloudflared any main" \
    > /etc/apt/sources.list.d/cloudflared.list
  apt-get update -qq
  DEBIAN_FRONTEND=noninteractive apt-get install -y -qq cloudflared >/dev/null
fi

say "running the tunnel as a service"
cloudflared service uninstall >/dev/null 2>&1 || true
cloudflared service install "$TOKEN" >/dev/null
systemctl enable --now cloudflared >/dev/null 2>&1 || true

say "setting CTRANSLATE's public address to https://$NAME"
systemctl stop ctranslate
sudo -u ctranslate python3 - "$DATA/settings.json" "https://$NAME" << 'PY'
import json, os, sys
p, url = sys.argv[1], sys.argv[2]
try:
    s = json.load(open(p))
except FileNotFoundError:
    s = {}
s['publicUrl'] = url
tmp = p + '.tmp'
with open(tmp, 'w') as f:
    json.dump(s, f, indent=1)
os.chmod(tmp, 0o600)
os.replace(tmp, p)
PY
systemctl start ctranslate

say "checking https://$NAME (the first connection can take a minute)"
ok=false
for _ in $(seq 1 30); do
  if curl -fsS --max-time 5 "https://$NAME/api/config" 2>/dev/null | grep -q '"build"'; then ok=true; break; fi
  sleep 3
done
if $ok; then
  say "done: https://$NAME/admin   (QR codes now point at https://$NAME)"
else
  echo "!! https://$NAME does not answer yet. In the Cloudflare dashboard, check the tunnel is" >&2
  echo "   HEALTHY and its public hostname $NAME points to HTTP localhost:8454." >&2
  echo "   Logs: journalctl -u cloudflared -n 50" >&2
fi
echo "   For \"guests only on the company Wi-Fi\": Admin -> Settings -> Factory internet"
echo "   address -> USE MY CURRENT ADDRESS (pressed from a phone or PC on the factory Wi-Fi)."
