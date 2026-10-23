#!/bin/bash
# ============================================================
#  CTRANSLATE - SERVER INSTALL (Ubuntu 22.04 / 24.04)
#
#      sudo bash install.sh                 install, or update in place
#      sudo bash install.sh --port 443      listen on 443 instead of 8453
#
#  Code goes to /opt/ctranslate, data to /var/lib/ctranslate (kept across updates),
#  optional API keys to /etc/ctranslate/ctranslate.env. Runs as the unprivileged
#  user 'ctranslate' under systemd (ctranslate.service). Standard-library Python only:
#  nothing from pip.
#
#  Then, for phones to trust it (the microphone needs real HTTPS):
#    sudo bash /opt/ctranslate/tools/enable-tunnel.sh <token> <name>   Cloudflare Tunnel (recommended)
#    sudo bash /opt/ctranslate/tools/enable-public-https.sh <name>     Let's Encrypt on the LAN
# ============================================================
set -euo pipefail
[ "$(id -u)" -eq 0 ] || { echo "Please run:  sudo bash install.sh"; exit 1; }
SRC="$(cd "$(dirname "$0")" && pwd)"
DEST=/opt/ctranslate
DATA=/var/lib/ctranslate
ETC=/etc/ctranslate
PORT=8453
TUNNEL_PORT=8454
while [ $# -gt 0 ]; do
  case "$1" in
    --port) PORT="$2"; shift 2 ;;
    --tunnel-port) TUNNEL_PORT="$2"; shift 2 ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
done
say() { echo "== $*"; }

# ---- prerequisites: python3 and openssl, nothing else ----
missing=()
command -v python3 >/dev/null || missing+=(python3)
command -v openssl >/dev/null || missing+=(openssl)
command -v curl >/dev/null || missing+=(curl)
if [ ${#missing[@]} -gt 0 ]; then
  say "installing ${missing[*]}"
  apt-get update -qq && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq "${missing[@]}" >/dev/null
fi
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 8) else 1)' \
  || { echo "!! Python 3.8 or newer is needed" >&2; exit 1; }

# ---- service user and directories ----
id -u ctranslate >/dev/null 2>&1 || useradd --system --home "$DATA" --shell /usr/sbin/nologin ctranslate
install -d -m 755 "$DEST"
install -d -m 750 -o ctranslate -g ctranslate "$DATA" "$DATA/certs"
install -d -m 750 -o root -g ctranslate "$ETC"

# ---- code (replaced on every run; data is never touched) ----
say "installing the application into $DEST"
rm -rf "$DEST/public.new"
cp -r "$SRC/public" "$DEST/public.new"
rm -rf "$DEST/public" && mv "$DEST/public.new" "$DEST/public"
install -m 644 "$SRC/server.py" "$DEST/server.py"
install -d -m 755 "$DEST/tools"
install -m 755 "$SRC"/tools/*.sh "$DEST/tools/" 2>/dev/null || true
install -m 644 "$SRC/README.md" "$DEST/README.md" 2>/dev/null || true
chown -R root:root "$DEST"

# ---- the console setup menu, and a login banner that names the address ----
install -m 755 "$SRC/tools/ctranslate-setup.sh" /usr/local/sbin/ctranslate-setup
if [ -d /etc/issue.d ] || mkdir -p /etc/issue.d 2>/dev/null; then
  cat > /etc/issue.d/ctranslate.issue << ISSUE

  CTRANSLATE   admin page: https://\\4$( [ "$PORT" = 443 ] || echo ":$PORT")/admin
          setup menu: sign in, then  sudo ctranslate-setup

ISSUE
fi

# ---- API keys (optional; the admin page can hold them instead) ----
if [ ! -f "$ETC/ctranslate.env" ]; then
  cat > "$ETC/ctranslate.env" << 'ENV'
# CTRANSLATE API keys. Optional: keys entered on the admin page (Settings) take
# precedence and are kept in /var/lib/ctranslate/settings.json instead.
# After editing:  sudo systemctl restart ctranslate
#GOOGLE_API_KEY=
#ANTHROPIC_API_KEY=
#AZURE_SPEECH_KEY=
ENV
fi
chown root:ctranslate "$ETC/ctranslate.env"; chmod 640 "$ETC/ctranslate.env"

# ---- a certificate to start with ----
# Self-signed: enough for the service to come up and for a PC to try it. Phones need
# a trusted one for the microphone: enable-tunnel.sh or enable-public-https.sh.
if [ ! -s "$DATA/certs/server.crt" ] || [ ! -s "$DATA/certs/server.key" ]; then
  say "making a temporary self-signed certificate"
  SAN="DNS:$(hostname),DNS:localhost,IP:127.0.0.1"
  for ip in $(hostname -I 2>/dev/null); do case "$ip" in *:*) ;; *) SAN="$SAN,IP:$ip" ;; esac; done
  openssl req -x509 -newkey rsa:2048 -nodes -days 397 -subj "/CN=$(hostname)" \
    -addext "subjectAltName=$SAN" -addext "extendedKeyUsage=serverAuth" \
    -keyout "$DATA/certs/server.key" -out "$DATA/certs/server.crt" >/dev/null 2>&1
  chown ctranslate:ctranslate "$DATA/certs/server.key" "$DATA/certs/server.crt"
  chmod 600 "$DATA/certs/server.key"
fi

# ---- persistent journal: after a power cut, the log of why is still there ----
install -d /etc/systemd/journald.conf.d
cat > /etc/systemd/journald.conf.d/ctranslate.conf << 'J'
# Written by CTRANSLATE install.sh
[Journal]
Storage=persistent
SystemMaxUse=200M
J
install -d /var/log/journal
systemctl restart systemd-journald >/dev/null 2>&1 || true

# ---- the service ----
CAPS=""
[ "$PORT" -lt 1024 ] && CAPS="AmbientCapabilities=CAP_NET_BIND_SERVICE"
cat > /etc/systemd/system/ctranslate.service << UNIT
[Unit]
Description=CTRANSLATE live translated meetings
After=network-online.target
Wants=network-online.target

[Service]
User=ctranslate
Group=ctranslate
EnvironmentFile=-$ETC/ctranslate.env
ExecStart=/usr/bin/python3 $DEST/server.py --port $PORT --tunnel-port $TUNNEL_PORT --data $DATA --cert $DATA/certs/server.crt --key $DATA/certs/server.key
Restart=always
RestartSec=2
$CAPS
# Every listener holds a connection open for the whole meeting.
LimitNOFILE=65536
NoNewPrivileges=yes
ProtectSystem=strict
ProtectHome=yes
PrivateTmp=yes
ReadWritePaths=$DATA
UMask=0077

[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload
systemctl enable ctranslate >/dev/null 2>&1
systemctl restart ctranslate

# ---- firewall: open the HTTPS port if ufw is on (the tunnel port stays local) ----
if command -v ufw >/dev/null && ufw status 2>/dev/null | grep -q "Status: active"; then
  ufw allow "$PORT/tcp" >/dev/null && say "ufw: opened $PORT/tcp"
fi

for _ in $(seq 1 50); do [ -s "$DATA/admin-pin.txt" ] && break; sleep 0.1; done
sleep 1
if ! systemctl is-active --quiet ctranslate; then
  echo "!! ctranslate did not start. See:  journalctl -u ctranslate -n 50" >&2
  exit 1
fi

IP=$(hostname -I 2>/dev/null | awk '{print $1}')
SUFFIX=""; [ "$PORT" != 443 ] && SUFFIX=":$PORT"
echo ""
echo "============================================================"
echo "  CTRANSLATE is running (build $(grep -o "BUILD_VERSION = '[^']*'" $DEST/server.py | cut -d"'" -f2))."
echo ""
echo "  On this network:   https://$IP$SUFFIX/admin"
echo "                     (temporary certificate: the browser warns until you"
echo "                      run enable-tunnel.sh or enable-public-https.sh)"
if [ -s "$DATA/admin-pin.txt" ]; then
echo "  Admin sign-in:     name  Admin"
echo "                     PIN   $(cat "$DATA/admin-pin.txt")"
echo "                     (also in $DATA/admin-pin.txt; change it on the admin page)"
fi
echo ""
echo "  Next:"
echo "   1. Public HTTPS (phones anywhere, no open ports) - Cloudflare Tunnel:"
echo "        sudo bash $DEST/tools/enable-tunnel.sh <tunnel-token> meet.example.com"
echo "      or HTTPS on the LAN with Let's Encrypt (DNS on Cloudflare):"
echo "        sudo bash $DEST/tools/enable-public-https.sh meet-lan.example.com"
echo "   2. Admin page -> Settings: Google API key, Anthropic API key."
echo "   3. Speak app -> INSTANT QR MEETING -> PRINT QR SHEET."
echo "  The console setup menu does the above step by step:  sudo ctranslate-setup"
echo "============================================================"
