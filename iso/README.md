# CTRANSLATE server installer ISO

`build-iso.sh` turns the Ubuntu Server 24.04 ISO into a CTRANSLATE installer. Write it to a USB stick, boot the server machine from it, and it installs Ubuntu and CTRANSLATE together. Nothing needs the internet during the install.

## Download it

GitHub builds the ISO for you: [Releases](https://github.com/wesprojects/CTRANSLATE/releases). GitHub keeps each release file under 2 GB, so the ISO comes in parts: download every part and `join-iso.bat` into one folder, then double-click `join-iso.bat` (Windows; it prints the SHA-256 to compare with the release notes), or `cat ctranslate-server-installer.iso.part* > ctranslate-server-installer.iso` (Mac, Linux). The repository is public, so the release is too; the ISO holds no keys or PINs.

For a new release after changes: **Actions → Build installer ISO → Run workflow** on `main` (`.github/workflows/iso.yml`; it runs the tests first, about 20 minutes in all). It replaces the release of the same build.

## Build it yourself

On any Linux machine (Ubuntu, Debian, or WSL on Windows), from the repository folder:

```bash
sudo apt install -y xorriso python3 curl
bash iso/build-iso.sh
```

It downloads the current Ubuntu Server 24.04 ISO once (about 3 GB, checked against Ubuntu's SHA256SUMS, kept in `~/.cache/ctranslate-iso`). It adds CTRANSLATE, plus `cloudflared` and `acme.sh` so the tunnel and certificate set up without the internet. It writes `ctranslate-server-installer.iso` and checks it: the boot menu, the installer answers (parsed as YAML when PyYAML is installed), the CTRANSLATE files, and the boot records for BIOS and UEFI.

| Option | Effect |
|---|---|
| `--password PASS` | Adds a no-questions install entry that uses this login password (hashed SHA-512 on the build machine) |
| `--password-hash HASH` | The same, with a `$6$` hash made elsewhere |
| `--user NAME` | Login account for that entry (default `ctadmin`; `ctranslate` is taken by the service) |
| `--hostname NAME` | Machine name for that entry (default `ctranslate-server`) |
| `--auto-default` | Make the no-questions entry the default; it starts after 10 seconds |
| `--iso PATH` | Use an Ubuntu Server 24.04 ISO already on disk |
| `--out FILE` | Output file (default `./ctranslate-server-installer.iso`) |
| `--serial-console` | Also send the installer's output to the serial port (VMs, headless boxes) |
| `--no-downloads` | Leave `cloudflared` and `acme.sh` off the media; the server fetches them when needed |
| `--test-boot` | Boot the result in QEMU against a scratch disk; the admin page appears at `https://localhost:18453/admin` |
| `--keep-work` | Keep the work folder `.build-iso` (with `xorriso.log`) |

The ISO carries CTRANSLATE as it is in this folder when you build, so build again after pulling changes.

## Write the USB stick

- **Windows:** [Rufus](https://rufus.ie) (choose *DD image mode* if it asks) or [balenaEtcher](https://etcher.balena.io).
- **Linux:** `sudo dd if=ctranslate-server-installer.iso of=/dev/sdX bs=4M status=progress conv=fsync`. Use the whole disk (`/dev/sdb`), not a partition; this erases the stick.

## Install

Boot the server from the stick (usually F12, F11 or Esc at power-on chooses the boot device). The boot menu:

| Entry | What it does |
|---|---|
| **Install CTRANSLATE Server** (default) | The Ubuntu installer asks two things: which disk, then the login account (your name, a password, the machine's name). Nothing is erased until you confirm the disk. Everything else is automatic. |
| **Install CTRANSLATE Server automatically** | Only on media built with `--password`. **Erases the largest disk** and installs with no questions. For a dedicated machine. |
| **Ubuntu Server only** | The normal Ubuntu installer, without CTRANSLATE. |
| Boot from next volume, UEFI Firmware Settings, Test memory | As on Ubuntu's own media |

The install takes about 10 to 20 minutes. The machine then restarts; **remove the stick** when it does (or set the disk first in the boot order).

### First boot

On the first start, the screen shows CTRANSLATE being installed from the copy on the disk (`/var/log/ctranslate-first-boot.log`), with no internet needed. Then the **setup menu** opens on the same screen and walks through:

1. **Status**: the admin page address and the first admin PIN. Write the PIN down.
2. **Time zone**.
3. **Network**: keep the automatic address (give the server a DHCP reservation on the router), or set a fixed one.
4. **Cloudflare Tunnel**: paste the tunnel token and the public name (see the main README for the two minutes in the Cloudflare dashboard). This gives phones the trusted HTTPS address they need.
5. **API keys**: Google and Anthropic. They can also be typed on the admin page.

Every step can be skipped. The menu is there any time afterward: sign in at the console or over SSH, and run

```bash
sudo ctranslate-setup
```

It also sets a new admin PIN, gets a Let's Encrypt certificate for the LAN name, restarts CTRANSLATE, shows the logs, and restarts or shuts down the server. The console login screen shows the admin page address.

## If something goes wrong

```bash
sudo cat /var/log/ctranslate-first-boot.log
systemctl status ctranslate-firstboot ctranslate
journalctl -u ctranslate -n 50
```

- **The installer asked about language and keyboard**: the boot entry lost its `autoinstall` parameter. The build checks this; rebuild and read its output.
- **No network**: the installer configures wired ports only (names starting with `e`). Check the cable, then `sudo ctranslate-setup` → Network.
- **First boot keeps retrying**: it tries every 30 seconds while `install.sh` fails; the log says why.
- `/opt/ctranslate-media/app` holds the CTRANSLATE copy from the stick. `sudo bash /opt/ctranslate-media/app/install.sh` installs it again (data in `/var/lib/ctranslate` is kept).

## Files

```
build-iso.sh             builds and checks the ISO
user-data                installer answers; the build makes two copies (ask, auto)
ctranslate-firstboot.sh       first boot: installs CTRANSLATE from /opt/ctranslate-media, then opens the menu
ctranslate-firstboot.service  runs it on tty1 until it succeeds (every 30 s, at most 20 times in 30 min)
ctranslate-welcome.service    the setup walk-through on tty1; the login prompt returns when it ends
```

The setup menu itself is `../tools/ctranslate-setup.sh`; `install.sh` installs it as `/usr/local/sbin/ctranslate-setup` on every server, however CTRANSLATE was installed.
