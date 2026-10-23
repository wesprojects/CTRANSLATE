#!/bin/bash
# A Let's Encrypt certificate for CTRANSLATE's own HTTPS port, for phones that reach the
# server directly on the LAN (no Cloudflare Tunnel in the path). DNS-01 through
# Cloudflare, so the server needs no inbound port from the internet.
#
#   sudo bash enable-public-https.sh <name> [--staging]
#   sudo bash enable-public-https.sh --status
#
# Before issuing:
#   - an A record for <name> -> this server's LAN address, Cloudflare proxy OFF (grey cloud)
#   - a DHCP reservation for this server, so that address doesn't move
#   - a Cloudflare API token with Zone:DNS:Edit on the zone (the script asks for it once)
# acme.sh, its account and the token live in /var/lib/ctranslate-acme (root only). A daily
# systemd timer renews; CTRANSLATE picks up the new certificate within a minute, no restart.
# --staging tests against Let's Encrypt's staging service and installs nothing.
set -euo pipefail
[ "$(id -u)" -eq 0 ] || { echo "run as root: sudo bash $0 ..." >&2; exit 1; }
unset SUDO_USER SUDO_UID SUDO_GID SUDO_COMMAND   # acme.sh refuses "sudo bash" otherwise
ACME_VER=3.1.1
HOME_DIR=/var/lib/ctranslate-acme
CERTS=/var/lib/ctranslate/certs
say() { echo "== $*"; }

if [ "${1:-}" = "--status" ]; then
  openssl x509 -in "$CERTS/server.crt" -noout -subject -issuer -enddate 2>/dev/null || echo "no certificate at $CERTS/server.crt"
  systemctl list-timers ctranslate-cert.timer --no-pager 2>/dev/null | head -3 || true
  exit 0
fi
NAME="${1:-}"; STAGING=false; [ "${2:-}" = "--staging" ] && STAGING=true
[ -n "$NAME" ] || { echo "usage: enable-public-https.sh <name> [--staging] | --status" >&2; exit 2; }
NAME=$(printf '%s' "$NAME" | tr '[:upper:]' '[:lower:]')
printf '%s' "$NAME" | grep -Eqx '([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,}' || { echo "not a DNS name: $NAME" >&2; exit 2; }
umask 077
install -d -m 700 "$HOME_DIR"

if [ ! -x "$HOME_DIR/acme.sh" ]; then
  say "fetching acme.sh $ACME_VER"
  tmp=$(mktemp -d)
  if [ -s "/opt/ctranslate/pkgs/acme.sh-$ACME_VER.tar.gz" ]; then
    tar -xzf "/opt/ctranslate/pkgs/acme.sh-$ACME_VER.tar.gz" -C "$tmp"      # carried on the install media
  else
    curl -fsSL --max-time 120 "https://github.com/acmesh-official/acme.sh/archive/refs/tags/$ACME_VER.tar.gz" | tar -xz -C "$tmp"
  fi
  ( cd "$tmp/acme.sh-$ACME_VER" && ./acme.sh --install --home "$HOME_DIR" --nocron --noprofile >/dev/null )
  rm -rf "$tmp"
fi

if [ ! -s "$HOME_DIR/cf-token" ]; then
  read -r -s -p "Cloudflare API token (Zone:DNS:Edit): " TOK; echo
  [ -n "$TOK" ] || { echo "no token" >&2; exit 2; }
  printf '%s' "$TOK" > "$HOME_DIR/cf-token"
fi
export CF_Token; CF_Token=$(cat "$HOME_DIR/cf-token")
SERVER=letsencrypt; $STAGING && SERVER=letsencrypt_test

say "issuing a certificate for $NAME ($SERVER)"
set +e
"$HOME_DIR/acme.sh" --home "$HOME_DIR" --issue --dns dns_cf -d "$NAME" --server "$SERVER" --keylength ec-256 --days 30
rc=$?
set -e
[ $rc -eq 0 ] || [ $rc -eq 2 ] || { echo "!! issuing failed (acme.sh exit $rc); nothing was installed" >&2; exit 1; }
if $STAGING; then say "staging issue worked; run again without --staging"; exit 0; fi

say "installing it for CTRANSLATE"
[ -f "$CERTS/server.crt" ] && cp -a "$CERTS/server.crt" "$CERTS/server.crt.selfsigned.bak" 2>/dev/null || true
"$HOME_DIR/acme.sh" --home "$HOME_DIR" --install-cert -d "$NAME" --ecc \
  --key-file "$CERTS/server.key" --fullchain-file "$CERTS/server.crt" \
  --reloadcmd "chown ctranslate:ctranslate $CERTS/server.key $CERTS/server.crt && chmod 600 $CERTS/server.key"

cat > /etc/systemd/system/ctranslate-cert.service << UNIT
[Unit]
Description=Renew CTRANSLATE's Let's Encrypt certificate (acme.sh)
[Service]
Type=oneshot
Environment=CF_Token=$CF_Token
ExecStart=$HOME_DIR/acme.sh --cron --home $HOME_DIR
UNIT
chmod 600 /etc/systemd/system/ctranslate-cert.service
cat > /etc/systemd/system/ctranslate-cert.timer << 'UNIT'
[Unit]
Description=Daily check of CTRANSLATE's certificate
[Timer]
OnCalendar=daily
RandomizedDelaySec=2h
Persistent=true
[Install]
WantedBy=timers.target
UNIT
systemctl daemon-reload
systemctl enable --now ctranslate-cert.timer >/dev/null

PORT=$(grep -o -- '--port [0-9]*' /etc/systemd/system/ctranslate.service | awk '{print $2}')
SUFFIX=""; [ "${PORT:-443}" != 443 ] && SUFFIX=":$PORT"
say "done. CTRANSLATE loads the new certificate within a minute: https://$NAME$SUFFIX/admin"
echo "   If you don't use a Cloudflare Tunnel, set Admin -> Settings -> Public address to"
echo "   https://$NAME$SUFFIX so QR codes point here."
