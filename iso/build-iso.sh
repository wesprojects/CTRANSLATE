#!/bin/bash
# ============================================================
#  CTRANSLATE - BUILD THE SERVER INSTALLER ISO
#
#      bash iso/build-iso.sh                         ISO that asks for the disk and account
#      bash iso/build-iso.sh --password 'secret'     adds a no-questions install entry
#      bash iso/build-iso.sh --test-boot             then boots it in QEMU
#
#  Run on any Linux machine (Ubuntu desktop or server, WSL works). It does not touch
#  the server: it writes ctranslate-server-installer.iso, which you write to a USB stick
#  (Rufus, balenaEtcher, dd) or attach to a VM.
#
#  Needs: xorriso, python3, curl   (sudo apt install -y xorriso python3 curl)
#  Optional: qemu-system-x86 and ovmf for --test-boot; mkpasswd (whois) or openssl
#  for --password.
# ============================================================
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP="$(cd "$HERE/.." && pwd)"
UBUNTU=24.04
ISO=""
OUT="$PWD/ctranslate-server-installer.iso"
PASSWORD=""; PASSHASH=""; USERNAME="ctadmin"; HOSTNAME_="ctranslate-server"
AUTO_DEFAULT=false; TEST_BOOT=false; KEEP=false; SERIAL=false; DOWNLOADS=true
CACHE="${XDG_CACHE_HOME:-$HOME/.cache}/ctranslate-iso"
CLOUDFLARED_URL="https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64.deb"
ACME_VER=3.1.1

usage() {
  cat << 'U'
usage: bash iso/build-iso.sh [options]

  --iso PATH            use this Ubuntu Server 24.04 ISO (default: download and check it)
  --out FILE            output file (default ./ctranslate-server-installer.iso)
  --password PASS       add a no-questions install entry with this login password
  --password-hash HASH  the same with a SHA-512 crypt hash made elsewhere ($6$...)
  --user NAME           login account for the no-questions entry (default ctadmin)
  --hostname NAME       machine name for the no-questions entry (default ctranslate-server)
  --auto-default        make the no-questions entry the default (boots it after 10 s)
  --serial-console      also send the installer's output to ttyS0 (115200)
  --no-downloads        don't put cloudflared and acme.sh on the media
  --test-boot           boot the result in QEMU against a scratch disk
  --keep-work           keep the work folder (.build-iso next to the output)
U
}
while [ $# -gt 0 ]; do
  case "$1" in
    --iso) ISO="$2"; shift 2 ;;
    --out) OUT="$(cd "$(dirname "$2")" && pwd)/$(basename "$2")"; shift 2 ;;
    --password) PASSWORD="$2"; shift 2 ;;
    --password-hash) PASSHASH="$2"; shift 2 ;;
    --user) USERNAME="$2"; shift 2 ;;
    --hostname) HOSTNAME_="$2"; shift 2 ;;
    --auto-default) AUTO_DEFAULT=true; shift ;;
    --serial-console) SERIAL=true; shift ;;
    --no-downloads) DOWNLOADS=false; shift ;;
    --test-boot) TEST_BOOT=true; shift ;;
    --keep-work) KEEP=true; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
done
say() { echo "== $*"; }
die() { echo "!! $*" >&2; exit 1; }
for c in xorriso python3 curl sha256sum md5sum; do
  command -v "$c" >/dev/null || die "$c is missing. Install:  sudo apt install -y xorriso python3 curl"
done
[[ "$USERNAME" =~ ^[a-z][a-z0-9-]{0,31}$ ]] || die "--user: lowercase letters, digits and dashes"
[ "$USERNAME" != ctranslate ] || die "--user cannot be 'ctranslate': that is the service's own account"
[[ "$HOSTNAME_" =~ ^[a-zA-Z0-9][a-zA-Z0-9-]{0,62}$ ]] || die "--hostname: letters, digits and dashes"

# ---- the login password for the no-questions entry ----
if [ -n "$PASSWORD" ] && [ -z "$PASSHASH" ]; then
  if command -v mkpasswd >/dev/null; then PASSHASH=$(printf '%s' "$PASSWORD" | mkpasswd --method=SHA-512 --stdin)
  else PASSHASH=$(printf '%s\n' "$PASSWORD" | openssl passwd -6 -stdin); fi
fi
[ -z "$PASSHASH" ] || [[ "$PASSHASH" == '$6$'* ]] || die "--password-hash must be a SHA-512 crypt hash (starts with \$6\$)"
AUTO=false; [ -n "$PASSHASH" ] && AUTO=true
$AUTO_DEFAULT && ! $AUTO && die "--auto-default needs --password or --password-hash"

# ---- the Ubuntu ISO: downloaded once, checked against Ubuntu's SHA256SUMS ----
if [ -z "$ISO" ]; then
  mkdir -p "$CACHE"
  say "looking up the current Ubuntu Server $UBUNTU release"
  SUMS=$(curl -fsSL --max-time 30 "https://releases.ubuntu.com/$UBUNTU/SHA256SUMS") \
    || die "cannot reach releases.ubuntu.com. Download the Ubuntu Server $UBUNTU ISO yourself and pass --iso PATH"
  LINE=$(printf '%s\n' "$SUMS" | grep -E "ubuntu-$UBUNTU(\.[0-9]+)?-live-server-amd64\.iso" | sort -V -k2 | tail -1)
  [ -n "$LINE" ] || die "no live-server ISO listed in SHA256SUMS"
  WANT=${LINE%% *}; NAME=${LINE##*[ *]}
  ISO="$CACHE/$NAME"
  if [ ! -f "$ISO" ] || [ "$(sha256sum "$ISO" | cut -d' ' -f1)" != "$WANT" ]; then
    say "downloading $NAME (about 3 GB)"
    curl -fL --retry 3 -C - -o "$ISO.part" "https://releases.ubuntu.com/$UBUNTU/$NAME" || die "download failed; run again to resume"
    mv "$ISO.part" "$ISO"
  fi
  say "checking $NAME against Ubuntu's checksum"
  [ "$(sha256sum "$ISO" | cut -d' ' -f1)" = "$WANT" ] || { rm -f "$ISO"; die "checksum mismatch: the download was damaged and is removed; run again"; }
fi
[ -f "$ISO" ] || die "no ISO at $ISO"

WORK="$(dirname "$OUT")/.build-iso"
rm -rf "$WORK"; mkdir -p "$WORK/ctranslate/app" "$WORK/ctranslate/pkgs" "$WORK/ctranslate/ask" "$WORK/grub"
cleanup() { $KEEP || rm -rf "$WORK"; }
trap cleanup EXIT

# ---- the payload: CTRANSLATE as it is in this folder ----
say "copying CTRANSLATE $(grep -o "BUILD_VERSION = '[^']*'" "$APP/server.py" | cut -d"'" -f2)"
cp "$APP/server.py" "$APP/install.sh" "$APP/README.md" "$WORK/ctranslate/app/"
cp -r "$APP/public" "$APP/tools" "$WORK/ctranslate/app/"
find "$WORK/ctranslate/app" -name '__pycache__' -prune -exec rm -rf {} +
cp "$HERE/ctranslate-firstboot.sh" "$HERE/ctranslate-firstboot.service" "$HERE/ctranslate-welcome.service" "$WORK/ctranslate/"
if $DOWNLOADS; then
  say "adding cloudflared and acme.sh, so the tunnel and certificate install without the internet"
  if [ ! -s "$CACHE/cloudflared-linux-amd64.deb" ] || [ -n "$(find "$CACHE/cloudflared-linux-amd64.deb" -mtime +14 2>/dev/null)" ]; then
    mkdir -p "$CACHE"
    curl -fsSL --max-time 300 -o "$CACHE/cloudflared-linux-amd64.deb.part" "$CLOUDFLARED_URL" \
      && mv "$CACHE/cloudflared-linux-amd64.deb.part" "$CACHE/cloudflared-linux-amd64.deb" \
      || echo "   !! cloudflared download failed; the server will fetch it when the tunnel is set up"
  fi
  [ -s "$CACHE/cloudflared-linux-amd64.deb" ] && cp "$CACHE/cloudflared-linux-amd64.deb" "$WORK/ctranslate/pkgs/"
  if [ ! -s "$CACHE/acme.sh-$ACME_VER.tar.gz" ]; then
    curl -fsSL --max-time 120 -o "$CACHE/acme.sh-$ACME_VER.tar.gz" \
      "https://github.com/acmesh-official/acme.sh/archive/refs/tags/$ACME_VER.tar.gz" \
      || { rm -f "$CACHE/acme.sh-$ACME_VER.tar.gz"; echo "   !! acme.sh download failed; the server will fetch it when needed"; }
  fi
  [ -s "$CACHE/acme.sh-$ACME_VER.tar.gz" ] && cp "$CACHE/acme.sh-$ACME_VER.tar.gz" "$WORK/ctranslate/pkgs/"
fi

# ---- the two sets of answers ----
say "writing the installer answers"
python3 - "$HERE/user-data" "$WORK/ctranslate" "$AUTO" "$USERNAME" "$HOSTNAME_" "$PASSHASH" << 'PY'
import os, re, sys
tpl, out, auto, user, host, phash = sys.argv[1:7]
t = open(tpl).read()
def write(sub, interactive, identity, storage):
    d = os.path.join(out, sub)
    os.makedirs(d, exist_ok=True)
    s = t
    for name, val in (('INTERACTIVE', interactive), ('IDENTITY', identity), ('STORAGE', storage)):
        s, n = re.subn(r'^@%s@\n' % name, lambda _m: val, s, flags=re.M)
        assert n == 1, name
    open(os.path.join(d, 'user-data'), 'w').write(s)
    open(os.path.join(d, 'meta-data'), 'w').write('instance-id: ctranslate-server-%s\n' % sub)
# Asks: the disk (nothing is erased until it is confirmed there) and the login account.
write('ask', '  interactive-sections:\n    - storage\n    - identity\n',
      '', '  storage:\n    layout:\n      name: direct\n')
if auto == 'true':
    write('auto', '',
          '  identity:\n    hostname: %s\n    username: %s\n    password: "%s"\n' % (host, user, phash),
          '  storage:\n    layout:\n      name: direct\n      match:\n        size: largest\n')
PY

# ---- the boot menu ----
say "writing the boot menu"
xorriso -osirrox on -indev "$ISO" -extract /boot/grub/grub.cfg "$WORK/grub/orig.cfg" >/dev/null 2>&1 \
  || die "cannot read /boot/grub/grub.cfg from $ISO: is it an Ubuntu Server ISO?"
grep -q '/casper/vmlinuz' "$WORK/grub/orig.cfg" || die "$ISO has no /casper/vmlinuz entry: not an Ubuntu Server live ISO?"
CONSOLE=""; $SERIAL && CONSOLE="console=ttyS0,115200 console=tty0"
python3 - "$WORK/grub/orig.cfg" "$WORK/grub" "$AUTO" "$AUTO_DEFAULT" "$CONSOLE" << 'PY'
import sys, re
orig, out, auto, auto_default, console = sys.argv[1], sys.argv[2], sys.argv[3] == 'true', sys.argv[4] == 'true', sys.argv[5]
src = open(orig).read()
tail = src[src.index('grub_platform'):] if 'grub_platform' in src else ''
def entry(title, sub, extra=''):
    params = ' '.join(p for p in ('autoinstall', 'ds=nocloud\\;s=/cdrom/ctranslate/%s/' % sub, extra, console) if p)
    return ('menuentry "%s" {\n\tset gfxpayload=keep\n\tlinux\t/casper/vmlinuz %s ---\n\tinitrd\t/casper/initrd\n}\n'
            % (title, params))
entries = [entry('Install CTRANSLATE Server  (asks which disk, and the login)', 'ask')]
if auto:
    e = entry('Install CTRANSLATE Server automatically  (ERASES the largest disk, no questions)', 'auto')
    entries = [e] + entries if auto_default else entries + [e]
head = ('set timeout=%d\nset default=0\n\nloadfont unicode\n\n'
        'set menu_color_normal=white/black\nset menu_color_highlight=black/yellow\n\n') % (10 if auto_default else 30)
plain = ('menuentry "Ubuntu Server only  (no CTRANSLATE; the normal Ubuntu installer)" {\n\tset gfxpayload=keep\n'
         '\tlinux\t/casper/vmlinuz  ---\n\tinitrd\t/casper/initrd\n}\n')
open(out + '/grub.cfg', 'w').write(head + ''.join(entries) + plain + tail)
loop = ''.join(e.replace('/casper/vmlinuz ', '/casper/vmlinuz iso-scan/filename=${iso_path} ') for e in entries)
open(out + '/loopback.cfg', 'w').write(loop)
PY

# The disc's own check ("check disc for defects") reads md5sum.txt: keep it true.
MAPS=(-map "$WORK/ctranslate" /ctranslate -map "$WORK/grub/grub.cfg" /boot/grub/grub.cfg -map "$WORK/grub/loopback.cfg" /boot/grub/loopback.cfg)
if xorriso -osirrox on -indev "$ISO" -extract /md5sum.txt "$WORK/md5sum.txt" >/dev/null 2>&1; then
  chmod u+w "$WORK/md5sum.txt"
  for g in grub.cfg loopback.cfg; do
    sum=$(md5sum "$WORK/grub/$g" | cut -d' ' -f1)
    sed -i "s#^[0-9a-f]\{32\}  \./boot/grub/$g\$#$sum  ./boot/grub/$g#" "$WORK/md5sum.txt"
  done
  MAPS+=(-map "$WORK/md5sum.txt" /md5sum.txt)
fi

say "writing $(basename "$OUT")"
rm -f "$OUT"
xorriso -indev "$ISO" -outdev "$OUT" "${MAPS[@]}" -boot_image any replay -commit >"$WORK/xorriso.log" 2>&1 \
  || { tail -20 "$WORK/xorriso.log" >&2; die "xorriso failed (run with --keep-work to keep $WORK/xorriso.log)"; }

# ---- check what was written ----
say "checking the ISO"
V="$WORK/verify"; mkdir -p "$V"
fail=0
xorriso -osirrox on -indev "$OUT" -extract /boot/grub/grub.cfg "$V/grub.cfg" -extract /ctranslate/ask/user-data "$V/ud" \
  -extract /ctranslate/app/server.py "$V/server.py" >/dev/null 2>&1 || true
grep -q 'ds=nocloud\\;s=/cdrom/ctranslate/ask/' "$V/grub.cfg" 2>/dev/null && echo "   boot menu: CTRANSLATE entries present" || { echo "   MISSING: CTRANSLATE boot entries"; fail=1; }
grep -q 'interactive-sections' "$V/ud" 2>/dev/null && echo "   installer answers present" || { echo "   MISSING: /ctranslate/ask/user-data"; fail=1; }
cmp -s "$V/server.py" "$APP/server.py" && echo "   CTRANSLATE files present" || { echo "   MISSING or different: /ctranslate/app/server.py"; fail=1; }
python3 -c "import yaml" 2>/dev/null && for f in "$WORK"/ctranslate/*/user-data; do
  python3 -c "import yaml,sys; d=yaml.safe_load(open(sys.argv[1])); assert d['autoinstall']['version']==1" "$f" \
    && echo "   $(basename "$(dirname "$f")")/user-data parses" || { echo "   BAD YAML: $f"; fail=1; }
done
if xorriso -indev "$OUT" -report_el_torito plain 2>&1 | grep -qi 'el torito'; then echo "   bootable (El Torito: BIOS and UEFI)"; else echo "   NOT BOOTABLE"; fail=1; fi
[ $fail = 0 ] || die "the ISO failed its checks"

echo ""
echo "============================================================"
echo "  $OUT  ($(du -h "$OUT" | cut -f1))"
echo ""
echo "  Boot menu:"
grep '^menuentry' "$V/grub.cfg" | sed 's/^menuentry "\([^"]*\)".*/    - \1/; s/^menuentry .\(.*\). {/    - \1/'
echo ""
echo "  Write it to a USB stick (this erases the stick):"
echo "    Windows: Rufus or balenaEtcher.   Linux:  sudo dd if=$(basename "$OUT") of=/dev/sdX bs=4M status=progress conv=fsync"
echo "============================================================"

if $TEST_BOOT; then
  command -v qemu-system-x86_64 >/dev/null || die "--test-boot needs qemu:  sudo apt install -y qemu-system-x86 qemu-utils ovmf"
  DISK="$WORK/test-disk.qcow2"; KEEP=true
  qemu-img create -f qcow2 "$DISK" 25G >/dev/null
  ACCEL=(); [ -w /dev/kvm ] && ACCEL=(-enable-kvm -cpu host)
  say "booting in QEMU (admin page will be at https://localhost:18453/admin, SSH on localhost:2222)"
  qemu-system-x86_64 "${ACCEL[@]}" -m 4096 -smp 2 -drive file="$DISK",if=virtio -cdrom "$OUT" -boot once=d \
    -netdev user,id=n,hostfwd=tcp::18453-:8453,hostfwd=tcp::2222-:22 -device virtio-net-pci,netdev=n
fi
