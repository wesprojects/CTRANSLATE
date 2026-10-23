#!/bin/bash
# CTRANSLATE setup menu (console). Installed as /usr/local/sbin/ctranslate-setup.
#
#   sudo ctranslate-setup              the menu
#   sudo ctranslate-setup --welcome    first-boot walk-through, then the menu
#   ctranslate-setup --status          the status text, no menu (any user)
#
# Everything it does can also be done by hand; it calls the same scripts:
# tools/enable-tunnel.sh, tools/enable-public-https.sh, netplan, timedatectl.
# CTRANSLATE_SYSROOT prefixes the paths it reads and writes, and CTRANSLATE_NO_ROOT=1
# skips the root check, for tests.
set -uo pipefail
SYSROOT=${CTRANSLATE_SYSROOT:-}
DEST=$SYSROOT/opt/ctranslate
DATA=$SYSROOT/var/lib/ctranslate
ETC=$SYSROOT/etc/ctranslate
ENVF=$ETC/ctranslate.env
NETPLAN=$SYSROOT/etc/netplan
TITLE="CTRANSLATE setup"
export NEWT_COLORS='root=,black window=white,black border=yellow,black title=yellow,black textbox=white,black button=black,yellow actbutton=black,yellow compactbutton=white,black listbox=white,black actlistbox=black,yellow actsellistbox=black,yellow entry=white,black checkbox=white,black actcheckbox=black,yellow'

if [ "${1:-}" != "--status" ] && [ "${CTRANSLATE_NO_ROOT:-}" != 1 ] && [ "$(id -u)" -ne 0 ]; then
  exec sudo "$0" "$@"
fi
command -v whiptail >/dev/null || { echo "whiptail is missing: sudo apt install whiptail" >&2; exit 1; }

# ---------- helpers ----------
# whiptail's answers go to stderr; these return them on stdout and the exit code of the dialog.
ask()   { whiptail --title "$TITLE" --inputbox "$1" 12 74 "${2:-}" 3>&1 1>&2 2>&3; }
secret(){ whiptail --title "$TITLE" --passwordbox "$1" 11 74 3>&1 1>&2 2>&3; }
msg()   { whiptail --title "$TITLE" --msgbox "$1" "${2:-14}" 76; }
yes_no(){ whiptail --title "$TITLE" --yesno "$1" "${2:-12}" 74; }
show_file() { whiptail --title "$TITLE" --scrolltext --textbox "$1" 30 90; }
run_shown() {   # run a command on the plain console (it may ask questions), then wait for Enter
  clear
  echo "== $*"; echo
  "$@"; local rc=$?
  echo; read -r -p "Press Enter to go back to the menu. " _
  return $rc
}
port()      { grep -o -- '--port [0-9]*' "$SYSROOT/etc/systemd/system/ctranslate.service" 2>/dev/null | awk '{print $2}' || true; }
lan_ips()   { hostname -I 2>/dev/null | tr ' ' '\n' | grep -E '^[0-9]+\.' || true; }
setting()   { python3 -c "import json,sys;print(json.load(open(sys.argv[1])).get(sys.argv[2]) or '')" "$DATA/settings.json" "$1" 2>/dev/null || true; }
wired_ifaces() { for d in "$SYSROOT"/sys/class/net/*; do n=$(basename "$d"); case "$n" in e*) echo "$n";; esac; done; }

status_text() {
  local p sfx ip pub svc tun cert keys
  p=$(port); p=${p:-8453}; sfx=""; [ "$p" != 443 ] && sfx=":$p"
  svc=$(systemctl is-active ctranslate 2>/dev/null || true)
  tun=$(systemctl is-active cloudflared 2>/dev/null || true)
  pub=$(setting publicUrl)
  echo "CTRANSLATE service:     ${svc:-unknown}"
  echo "Build:             $(grep -o "BUILD_VERSION = '[^']*'" "$DEST/server.py" 2>/dev/null | cut -d"'" -f2)"
  echo
  echo "On this network:"
  for ip in $(lan_ips); do echo "   https://$ip$sfx/admin"; done
  [ -z "$(lan_ips)" ] && echo "   (no IPv4 address: check the cable, or Network in this menu)"
  echo
  echo "Public address:    ${pub:-not set (Cloudflare Tunnel in this menu)}"
  echo "Cloudflare Tunnel: ${tun:-not installed}"
  if [ -e "$DATA/certs/server.crt" ] && [ ! -r "$DATA/certs/server.crt" ]; then
    cert="(run with sudo to see it)"
  else
    cert=$(openssl x509 -in "$DATA/certs/server.crt" -noout -issuer -enddate 2>/dev/null | sed 's/^issuer=/issued by /; s/^notAfter=/expires /' | tr '\n' ';' | sed 's/;$//')
  fi
  echo "Certificate:       ${cert:-none}"
  keys=""
  for k in GOOGLE_API_KEY ANTHROPIC_API_KEY AZURE_SPEECH_KEY; do
    grep -q "^$k=." "$ENVF" 2>/dev/null && keys="$keys ${k%%_*}"
  done
  echo "Keys in ctranslate.env: ${keys:- none (they can also be set on the admin page)}"
  echo
  if [ -e "$DATA/admin-pin.txt" ] && [ ! -r "$DATA/admin-pin.txt" ]; then
    echo "First admin PIN:   run  sudo ctranslate-setup --status  to see it"
  elif [ -s "$DATA/admin-pin.txt" ]; then
    echo "First admin sign-in: name Admin, PIN $(cat "$DATA/admin-pin.txt")"
    echo "   (if it was changed on the admin page or here, the new PIN applies)"
  fi
  echo "Time:              $(date '+%Y-%m-%d %H:%M %Z')"
}

# ---------- menu items ----------
do_status() {
  local t; t=$(mktemp); status_text > "$t"; show_file "$t"; rm -f "$t"
}

do_pin() {
  local a b
  a=$(secret "New PIN for the account named Admin (6 to 12 digits):") || return
  [[ "$a" =~ ^[0-9]{6,12}$ ]] || { msg "A PIN is 6 to 12 digits. Nothing changed." 8; return; }
  b=$(secret "Type the new PIN again:") || return
  [ "$a" = "$b" ] || { msg "The two PINs differ. Nothing changed." 8; return; }
  systemctl stop ctranslate 2>/dev/null || true
  if CTRANSLATE_PIN="$a" python3 - "$DEST" "$DATA/users.json" << 'PY'
import json, os, sys
sys.path.insert(0, sys.argv[1])
import server                                  # hash_pin and set_pin, as the server does it
p = sys.argv[2]
users = json.load(open(p))
admins = [u for u in users.values() if u.get('name', '').casefold() == 'admin']
if not admins:
    admins = [u for u in users.values() if u.get('role') == 'admin']
if not admins:
    sys.exit('no admin account found')
server.set_pin(admins[0], os.environ['CTRANSLATE_PIN'])
admins[0]['active'] = True
tmp = p + '.tmp'
with open(tmp, 'w') as f:
    json.dump(users, f, ensure_ascii=False, indent=1)
os.chmod(tmp, 0o600)
os.replace(tmp, p)
print(admins[0]['name'])
PY
  then
    chown ctranslate:ctranslate "$DATA/users.json" 2>/dev/null || true
    rm -f "$DATA/admin-pin.txt"
    systemctl start ctranslate 2>/dev/null || true
    msg "The admin PIN is changed. Sign in with the name Admin and the new PIN." 8
  else
    systemctl start ctranslate 2>/dev/null || true
    msg "The PIN could not be changed (see above). Nothing changed." 8
  fi
}

do_network() {
  local ifs iface mode addr gw dns f
  mapfile -t ifs < <(wired_ifaces)
  [ ${#ifs[@]} -gt 0 ] || { msg "No wired network interface found." 8; return; }
  if [ ${#ifs[@]} -eq 1 ]; then iface=${ifs[0]}
  else
    local items=(); for i in "${ifs[@]}"; do items+=("$i" "$(cat "$SYSROOT/sys/class/net/$i/operstate" 2>/dev/null)"); done
    iface=$(whiptail --title "$TITLE" --menu "Which network port?" 16 60 6 "${items[@]}" 3>&1 1>&2 2>&3) || return
  fi
  mode=$(whiptail --title "$TITLE" --menu "Address for $iface" 14 74 2 \
    dhcp  "Automatic (DHCP) - give the server a reservation on the router" \
    fixed "Fixed address - typed in here" 3>&1 1>&2 2>&3) || return
  f=$NETPLAN/01-ctranslate.yaml
  if [ "$mode" = fixed ]; then
    addr=$(ask "Address with prefix length, for example 192.168.1.20/24" "$(ip -4 -o addr show "$iface" 2>/dev/null | awk '{print $4}' | head -1)") || return
    [[ "$addr" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+/[0-9]+$ ]] || { msg "That is not an address like 192.168.1.20/24. Nothing changed." 8; return; }
    gw=$(ask "Gateway (the router), for example 192.168.1.1" "$(ip -4 route show default 2>/dev/null | awk '{print $3}' | head -1)") || return
    [[ "$gw" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]] || { msg "That is not a gateway address. Nothing changed." 8; return; }
    dns=$(ask "DNS servers, separated by commas" "$gw, 1.1.1.1") || return
    dns=$(echo "$dns" | tr -d ' ')
    yes_no "Set $iface to $addr, gateway $gw, DNS $dns?\n\nIf you are connected over SSH, the connection may drop; reconnect at the new address." 12 || return
    umask 077
    cat > "$f.new" << NP
# Written by ctranslate-setup
network:
  version: 2
  ethernets:
    $iface:
      dhcp4: false
      addresses: [$addr]
      routes:
        - to: default
          via: $gw
      nameservers:
        addresses: [$dns]
NP
  else
    yes_no "Set $iface to get its address automatically (DHCP)?" 8 || return
    umask 077
    cat > "$f.new" << NP
# Written by ctranslate-setup
network:
  version: 2
  ethernets:
    $iface:
      dhcp4: true
NP
  fi
  # The installer's file would configure the port as well; set it aside, and stop
  # cloud-init from writing it back at the next boot.
  # Every earlier file, this menu's own included, is set aside, and comes back if netplan
  # refuses the new one.
  local aside=()
  for old in "$NETPLAN"/*.yaml; do
    [ -e "$old" ] && aside+=("$old")
  done
  for old in "${aside[@]}"; do mv "$old" "$old.ctranslate-off"; done
  mv "$f.new" "$f"; chmod 600 "$f"
  if netplan generate 2>/tmp/ctranslate-netplan.err; then
    mkdir -p "$SYSROOT/etc/cloud/cloud.cfg.d"
    echo 'network: {config: disabled}' > "$SYSROOT/etc/cloud/cloud.cfg.d/99-ctranslate-network.cfg"
    rm -f "$f.ctranslate-off"
    netplan apply 2>/dev/null || true
    sleep 3
    msg "Network set. Addresses now:\n\n$(lan_ips | sed 's/^/   /')" 12
  else
    rm -f "$f"
    for old in "${aside[@]}"; do mv "$old.ctranslate-off" "$old"; done
    msg "netplan refused the settings; the old ones are back.\n\n$(head -5 /tmp/ctranslate-netplan.err)" 14
  fi
}

do_timezone() {
  local tz cur
  cur=$(timedatectl show -p Timezone --value 2>/dev/null || echo UTC)
  tz=$(whiptail --title "$TITLE" --default-item "$cur" --menu "Time zone (now $cur)" 20 64 10 \
    America/New_York "Eastern" America/Chicago "Central" America/Denver "Mountain" \
    America/Phoenix "Arizona" America/Los_Angeles "Pacific" America/Anchorage "Alaska" \
    Pacific/Honolulu "Hawaii" UTC "UTC" other "Type another (Europe/Paris, Africa/Nairobi...)" 3>&1 1>&2 2>&3) || return
  if [ "$tz" = other ]; then tz=$(ask "Time zone name, for example Africa/Nairobi" "$cur") || return; fi
  if timedatectl set-timezone "$tz" 2>/dev/null; then
    systemctl restart ctranslate 2>/dev/null || true
    msg "Time zone set to $tz. Time is now $(date '+%H:%M')." 8
  else msg "Unknown time zone: $tz" 8; fi
}

do_tunnel() {
  local tok name
  msg "Cloudflare Tunnel gives CTRANSLATE a public HTTPS name (meet.yourdomain.com) that every phone trusts, with no port opened on the router.

First, in the Cloudflare dashboard: Zero Trust -> Networks -> Tunnels -> Create a tunnel (Cloudflared). Copy the token from the install command it shows. Then add a Public Hostname: your name, Service HTTP, URL localhost:8454.

Then come back here with the token and the name." 18
  tok=$(secret "Tunnel token (the long string after --token):") || return
  [ -n "$tok" ] || return
  name=$(ask "Public name, for example meet.yourdomain.com" "$(setting publicUrl | sed 's#^https://##')") || return
  run_shown bash "$DEST/tools/enable-tunnel.sh" "$tok" "$name"
}

do_cert() {
  local name
  msg "A Let's Encrypt certificate for phones that reach the server directly on the factory Wi-Fi.

Before: in Cloudflare DNS, an A record (for example meet-lan) pointing at this server's address, $(lan_ips | head -1), with the proxy OFF; and a Cloudflare API token with Zone -> DNS -> Edit. The next screen asks for the token.

Not needed if everyone uses the Cloudflare Tunnel name." 16
  name=$(ask "Name for this server on the LAN, for example meet-lan.yourdomain.com") || return
  [ -n "$name" ] || return
  run_shown bash "$DEST/tools/enable-public-https.sh" "$name"
}

set_env_key() {   # $1 name, $2 value ('' removes)
  mkdir -p "$ETC"; touch "$ENVF"
  grep -v -E "^#?$1=" "$ENVF" > "$ENVF.new" || true
  [ -n "$2" ] && printf '%s=%s\n' "$1" "$2" >> "$ENVF.new"
  mv "$ENVF.new" "$ENVF"
  chown root:ctranslate "$ENVF" 2>/dev/null || true; chmod 640 "$ENVF"
}
do_keys() {
  local k label v changed=false
  while true; do
    k=$(whiptail --title "$TITLE" --menu "API keys (stored in /etc/ctranslate/ctranslate.env, readable only by CTRANSLATE).\nKeys typed on the admin page take precedence over these." 18 78 5 \
      GOOGLE_API_KEY    "Google Cloud: voices, translation, speech $(grep -q '^GOOGLE_API_KEY=.' "$ENVF" 2>/dev/null && echo '[set]')" \
      ANTHROPIC_API_KEY "Anthropic: Claude translation $(grep -q '^ANTHROPIC_API_KEY=.' "$ENVF" 2>/dev/null && echo '[set]')" \
      AZURE_SPEECH_KEY  "Azure Speech: backup voices (optional) $(grep -q '^AZURE_SPEECH_KEY=.' "$ENVF" 2>/dev/null && echo '[set]')" \
      back              "Back" 3>&1 1>&2 2>&3) || break
    [ "$k" = back ] && break
    label=$k
    v=$(secret "$label (leave empty to remove it):") || continue
    set_env_key "$k" "$(echo "$v" | tr -d '[:space:]')"
    changed=true
  done
  if $changed; then systemctl restart ctranslate 2>/dev/null || true; msg "Keys saved; CTRANSLATE restarted. Check them on the admin page: Settings -> TEST ENGINES." 9; fi
}

do_service() {
  local c t
  c=$(whiptail --title "$TITLE" --menu "CTRANSLATE service" 14 64 4 \
    restart "Restart CTRANSLATE" log "Show the last 200 log lines" tunnel "Show the tunnel's log" back "Back" 3>&1 1>&2 2>&3) || return
  case "$c" in
    restart) systemctl restart ctranslate; sleep 1; msg "CTRANSLATE is $(systemctl is-active ctranslate)." 8 ;;
    log) t=$(mktemp); journalctl -u ctranslate -n 200 --no-pager > "$t" 2>&1; show_file "$t"; rm -f "$t" ;;
    tunnel) t=$(mktemp); journalctl -u cloudflared -n 200 --no-pager > "$t" 2>&1; show_file "$t"; rm -f "$t" ;;
  esac
}

menu() {
  local c ip p head
  while true; do
    ip=$(lan_ips | head -1); p=$(port); p=${p:-8453}
    head="Admin page: https://${ip:-(no address yet)}$( [ "$p" = 443 ] || echo ":$p")/admin"
    c=$(whiptail --title "$TITLE" --cancel-button "Exit" --menu "$head" 22 76 12 \
      status   "Status: addresses, links, what is set up" \
      pin      "Admin PIN: set a new one" \
      network  "Network: automatic or a fixed address" \
      timezone "Time zone" \
      tunnel   "Cloudflare Tunnel: public HTTPS name" \
      cert     "Let's Encrypt certificate for the LAN" \
      keys     "API keys: Google, Anthropic, Azure" \
      service  "Service: restart, logs" \
      reboot   "Restart this server" \
      poweroff "Shut down this server" \
      shell    "Exit to the command line" 3>&1 1>&2 2>&3) || return 0
    case "$c" in
      status) do_status ;; pin) do_pin ;; network) do_network ;; timezone) do_timezone ;;
      tunnel) do_tunnel ;; cert) do_cert ;; keys) do_keys ;; service) do_service ;;
      reboot) yes_no "Restart the server now? Meetings in progress are cut off." 8 && systemctl reboot ;;
      poweroff) yes_no "Shut the server down now? Meetings stop until it is switched on again." 8 && systemctl poweroff ;;
      shell) return 0 ;;
    esac
  done
}

welcome() {
  local t; t=$(mktemp)
  { echo "CTRANSLATE is installed and running."; echo; status_text; echo
    echo "Next, the menu walks through: time zone, network, the public HTTPS name"
    echo "(Cloudflare Tunnel) and the API keys. Each can be skipped and done later"
    echo "with:  sudo ctranslate-setup"; } > "$t"
  show_file "$t"; rm -f "$t"
  do_timezone
  yes_no "Keep getting the address automatically (DHCP)?\n\nChoose No to set a fixed address. With DHCP, give the server a reservation on the router so its address never moves." 12 || do_network
  yes_no "Set up the Cloudflare Tunnel now?\n\nIt gives phones a trusted HTTPS address; the microphone and the pinned apps need it. You need the tunnel token from the Cloudflare dashboard." 12 && do_tunnel
  yes_no "Enter the API keys now?\n\nGoogle (voices) and Anthropic (Claude translation). They can also be typed on the admin page, under Settings." 11 && do_keys
  do_status
}

case "${1:-}" in
  --status) status_text; exit 0 ;;
  --welcome) welcome; menu ;;
  *) menu ;;
esac
clear 2>/dev/null || true
