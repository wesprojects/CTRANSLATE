# CTRANSLATE

Live translated meetings. A speaker talks in their own language; everyone else hears it and reads it on their phone in theirs: Spanish, French, Swahili and English to start, 18 languages available.

Three pinnable apps on iPhone, Android and PC, served by one Ubuntu server:

| App | Who | Address |
|---|---|---|
| **Listen** | Everyone listening: staff, or factory workers who scan a QR code | `/listen` |
| **Speak** | Speakers: start a meeting, talk, see who is listening | `/speak` |
| **Admin** | People, meetings, languages, voices, API keys | `/admin` |

---

## How it works

1. **The speaker talks.** Their phone or PC turns speech into text: the browser's own recognizer (free), or Google Speech-to-Text through the server (any microphone or headset, any browser).
2. **The server translates** each phrase once per language someone is listening in: Claude Haiku 5.5, with Google Cloud Translation and then Google's free endpoint as automatic fallbacks.
3. **The server voices it** in each language with Google Cloud Text-to-Speech (WaveNet voices where the language has them, Swahili included). If a voice fails, it falls back to Azure (when a key is set), then Google Translate's free read-aloud, then the phone's own voice.
4. **Each listener's phone** shows the text in their language and plays the voice through whatever is connected: earbuds, a Bluetooth headset or the phone speaker.

**Timing.** Words appear on listeners' screens about half a second to a second after they're spoken, while the speaker is still talking, and settle as the phrase finishes. The voice follows about 2 to 3 seconds behind the speaker, because a phrase has to be finished, translated, then voiced. Someone who talks without pausing is cut into phrases every 2 seconds, so the voice never falls far behind.

**Accuracy.** Machine translation and speech recognition both make mistakes, mostly with names, jargon and noisy rooms. Claude with the glossary (Admin → Settings) keeps company terms right. A headset microphone for the speaker helps more than anything else on a loud floor.

---

## Factory floor: QR meetings

No accounts and no approval for listeners:

1. Speaker opens **Speak** → **⚡ INSTANT QR MEETING** → **START NOW**.
2. **PRINT QR SHEET**: a large QR code, the meeting code, and "Scan to hear this meeting in your language" in every offered language.
3. A worker scans it with the phone camera. The first screen asks for their language, written in each language. They tap theirs, then one big button (**ESCUCHAR**, **ÉCOUTER**, **SIKILIZA**). Every word on their screen from then on is in their language.

**Standing room.** Tick this for a QR sign posted on the wall. The code never changes: ending a session puts the room back to "waiting for the next one", and phones that are still open start hearing the next session by themselves.

**Guests only on the company Wi-Fi.** With this ticked, a photo of the sign doesn't work from outside. Set the factory's internet address once: **Admin → Settings → Factory internet address → USE MY CURRENT ADDRESS**, pressed from a device on the factory Wi-Fi.

**NEW CODE** (in the QR sheet window) retires a sheet that went somewhere it shouldn't. Every printed copy stops working and guests on it are disconnected.

---

## Speaking

- **Tap to start, hold to stop.** One tap starts the microphone. Stopping takes a 1.5-second press; a ring fills around the button and the phone buzzes. A brush against the screen in a pocket or holster does nothing. Tap-to-stop and push-to-talk are in the dropdown.
- **Keep the screen on.** The app holds the screen awake while open. If the phone locks, the browser stops listening (an iOS and Android rule for web apps). Turn the brightness down rather than locking.
- **Headset microphones** work with either recognizer; the Google recognizer can also pick a specific microphone from a list.
- Other speakers' words appear in your language. Turn on **HEAR OTHERS** to hear them too; the voice pauses while your microphone is on, so it never feeds back into it.
- **⋮ → DOWNLOAD TRANSCRIPT**: everything said, with each language's translation, timestamped.

## Listening

- Tap the language, then the big button. Headphones work.
- **VOZ / VOIX / SAUTI** turns the voice off for text only; **A− / A+** sizes the text; **MORE** sets voice speed and shows the original words.
- The screen stays on while listening. With headphones in, the phone can go in a pocket: the voice keeps playing.
- Dropped Wi-Fi reconnects by itself and shows anything missed.

---

## Try it on a PC first

With Python 3 installed (Windows, Mac or Linux), from the repository folder:

```bash
python3 server.py --http --port 8080        # on Windows: py server.py --http --port 8080
```

It prints the admin PIN. Open `http://localhost:8080/admin` on that PC, enter the Google and Anthropic keys under **Settings**, and press **▶ PLAY** beside each language under **Hear each language**. You hear your sentence translated and spoken by the same voices meetings use. The microphone also works there, because browsers treat `localhost` as secure. Phones need the server and HTTPS below.

## Install from the ISO (recommended)

`bash iso/build-iso.sh` makes `ctranslate-server-installer.iso`: write it to a USB stick and boot the server from it. It installs Ubuntu Server 24.04 and CTRANSLATE together, with no internet needed. On the first start a setup menu walks through the time zone, network, Cloudflare Tunnel and API keys. See [iso/README.md](iso/README.md).

The same menu is on every CTRANSLATE server: `sudo ctranslate-setup`.

## Install on an existing Ubuntu (Ubuntu 22.04 or 24.04)

```bash
git clone https://github.com/wesprojects/CTRANSLATE.git && cd CTRANSLATE
sudo bash install.sh
```

It prints the admin sign-in (name **Admin**, an 8-digit PIN). Running it again updates the code and keeps all data.

| | |
|---|---|
| Code | `/opt/ctranslate` |
| Data | `/var/lib/ctranslate`: people, meetings, settings and keys (mode 0600), transcripts, certificate |
| Optional keys file | `/etc/ctranslate/ctranslate.env` |
| Service | `ctranslate.service` (user `ctranslate`); logs: `journalctl -u ctranslate -f` |
| Ports | 8453 HTTPS (LAN); 8454 plain HTTP on 127.0.0.1 only, for the tunnel |

### HTTPS that phones trust

The microphone only works over trusted HTTPS. Choose one or both.

**Cloudflare Tunnel (recommended).** A public name with a real certificate, and no port opened on the router.

1. Cloudflare dashboard → Zero Trust → Networks → Tunnels → **Create a tunnel** (Cloudflared). Copy the token from the install command it shows.
2. In the tunnel, **Public Hostname → Add**: `meet` . `yourdomain.com`, Service **HTTP**, URL **localhost:8454**.
3. On the server:
   ```bash
   sudo bash /opt/ctranslate/tools/enable-tunnel.sh <token> meet.yourdomain.com
   ```
   This installs `cloudflared` as a service and points QR codes at `https://meet.yourdomain.com`.

**Let's Encrypt on the LAN.** Phones reach the server directly over the Wi-Fi.

1. In Cloudflare DNS, add an A record `meet-lan` → the server's LAN address, proxy **off**. Give the server a DHCP reservation.
2. Create a Cloudflare API token with **Zone → DNS → Edit**.
3. On the server:
   ```bash
   sudo bash /opt/ctranslate/tools/enable-public-https.sh meet-lan.yourdomain.com
   ```
   It renews daily by itself; CTRANSLATE reloads the certificate without a restart.

### API keys (Admin → Settings)

**Google Cloud.** One key covers Text-to-Speech, Translation and Speech-to-Text.

1. console.cloud.google.com → create a project → link a billing account. The free allowances below still apply.
2. APIs & Services → Library → enable **Cloud Text-to-Speech API**, **Cloud Translation API** and **Cloud Speech-to-Text API**.
3. APIs & Services → Credentials → **Create credentials → API key** → **Restrict key** to those three APIs.

**Anthropic** (Claude translation): console.anthropic.com → API keys.

**Azure Speech** (optional backup voices): Azure portal → create a **Speech** resource → Keys and Endpoint. Enter the key and its region (for example `eastus`).

Press **TEST ENGINES** after saving. It shows which voice each language will use.

---

## Cost

A one-hour meeting is about 50,000 characters per listening language.

| Service | Free each month | After that | One hour, 3 languages |
|---|---|---|---|
| Google voices (WaveNet / Standard) | 4 million characters | $4 per million | about $0.60, free for roughly 25 hours a month |
| Claude Haiku 5.5 translation | none | $0.10 / $0.50 per million tokens | about $0.10 |
| Google Cloud Translation (fallback only) | 500,000 characters | $20 per million | only when Claude fails |
| Google Speech-to-Text (optional recognizer) | Google's monthly allowance | billed per minute | $0 with the browser recognizer |
| Browser recognizer, phone's voice, free endpoints | unlimited | free | $0 |

Check current prices on Google's and Anthropic's pricing pages.

---

## Requirements and known limits

- **Speakers:** Chrome or Edge (Android, Windows, Mac), Safari (iPhone, iPad, Mac). On iPhone, turn on Dictation (Settings → General → Keyboard). Firefox has no browser recognizer; ask the admin to turn on the Google recognizer.
- **Listeners:** any current phone browser.
- **Swahili voice** comes from Google Cloud. Google may offer it in fewer voice families than Spanish or French; CTRANSLATE takes the best Swahili voice Google lists, whatever the family setting. **TEST ENGINES** names the voice each language gets. iPhones have no Swahili voice of their own, so the phone's-own-voice backup reads Swahili as text only there.
- **Background audio** with the screen locked depends on the phone. Keeping the app open (screen on, brightness down) is the dependable setup; that's why the app holds the screen awake.
- **The listener-screen wording** in Spanish, French and Swahili, and the scan line printed on the QR sheet, should be checked by a native speaker before it goes on the wall (`public/i18n.js`).

## Tests

```bash
python3 tests/smoke_test.py                                   # server end to end, fake engines
NODE_PATH=$(npm root -g) node tests/browser_test.js [shots/]  # the real pages in Chromium (Playwright)
python3 tests/setup_test.py                                   # the console setup menu, in a sandbox
pip install numpy opencv-python-headless && python3 tests/qr_test.py   # QR codes decode
```

## Build format

`YYYY-MM-DD.N`: date of the revision, then the increment for that day. Shown under the app name.

---

Conklin Office Furniture · Research and Development
