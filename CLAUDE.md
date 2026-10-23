# CLAUDE.md — CTRANSLATE

Context for working on this app. [README.md](README.md) is the operator guide. Read this before changing anything.

## What it is

Live translated meetings: a speaker's phone or PC turns speech into text, the server translates each phrase once per listening language and voices it, and every listener's phone shows and plays it in their language. Three pinnable web apps (Listen, Speak, Admin) on one Ubuntu server. Owner: Wes, Product Development, Conklin Office Furniture.

## Layout

| Path | Role |
|---|---|
| `server.py` | The whole server. Python 3 standard library only (`ThreadingHTTPServer`); no pip. JSON API under `/api/`, live stream (Server-Sent Events), static pages, engines |
| `public/listen.html` | Listener app. Language first, then one LISTEN tap. All on-screen words come from `i18n.js` |
| `public/speak.html` | Speaker app: instant QR meetings, the console, both recognizers, the hold-to-stop mic |
| `public/admin.html` | Meetings, people, settings, API keys, engine test |
| `public/print.html` | Printable QR sheet (`/print?m=<id>`); two codes (① Wi-Fi, ② meeting) when the meeting has UniFi Wi-Fi |
| `public/app.js` | Shared client: API, sign-in, meeting editor, share sheet, audio `Player`, `Stream`, wake lock, build banner, install hint |
| `public/i18n.js` | Listener-screen words per language; `scan` = the line printed on the QR sheet, `scan2` = the same on a sheet with a Wi-Fi code first |
| `public/qr.js` | QR encoder (byte mode, level M, versions 1–10), no library |
| `public/sw.js` | Service worker, one per app scope, network first |
| `install.sh`, `tools/enable-tunnel.sh`, `tools/enable-public-https.sh` | Ubuntu install, Cloudflare Tunnel, Let's Encrypt (acme.sh, DNS-01 through Cloudflare) |
| `tools/make-icons.py` | Draws the app icons (stdlib PNG) |
| `tools/ctranslate-setup.sh` | Console setup menu (whiptail), installed as `/usr/local/sbin/ctranslate-setup`: status, admin PIN, network (netplan), time zone, tunnel, certificate, API keys (`/etc/ctranslate/ctranslate.env`), service. `--welcome` is the first-boot walk-through. `CTRANSLATE_SYSROOT`/`CTRANSLATE_NO_ROOT` for tests |
| `iso/` | `build-iso.sh` repacks the Ubuntu Server 24.04 ISO with xorriso: `/ctranslate/app` (this folder), `/ctranslate/pkgs` (cloudflared, acme.sh), `/ctranslate/ask` and `/ctranslate/auto` (autoinstall answers from `user-data`), and a new boot menu. Late-commands copy the app to `/opt/ctranslate-media`; `ctranslate-firstboot.service` runs `install.sh` from there, then `ctranslate-welcome.service` opens the menu on tty1 |
| `testkit/` | Home test kit: `START-TEST.bat` (Windows, CRLF), `start-test.sh`, `README-TEST.txt`. Runs `server.py --http --port 8080 --tunnel-port 8081 --demo --data testkit/data` |
| `tests/` | `smoke_test.py` (server), `browser_test.js` (Playwright), `qr_test.py` (OpenCV decodes every QR version and mask), `setup_test.py` (the setup menu with stand-in whiptail, systemctl and netplan), `unifi_test.py` (meeting Wi-Fi against a stand-in HTTPS UniFi console; `--serve` runs the stand-in alone), `unifi_browser_test.js` (the UniFi screens and the two-code sheet, the Wi-Fi code read back with OpenCV when installed), `demo_test.py` (`--demo`) |

## Non-negotiables

- **Build stamp** `YYYY-MM-DD.N` lives once, in `server.py` `BUILD_VERSION`; the server writes it into every page, script and manifest in place of `__BUILD__`. Check today's real date before bumping; the counter resets to `.1` on a new day. Every revision bumps it.
- **Standard library only** on the server. The installer must work on a bare Ubuntu with python3 and openssl.
- **House framework**: the `:root` tokens, the system mono type (no webfont link), the header (app name + build left, brand block right, copied from the other Conklin apps). Phone controls may be larger (`.btn.big`); the header may not change.
- **Verify by running, not parsing.** Run `tests/smoke_test.py` and `tests/browser_test.js` after any change; both use `CTRANSLATE_FAKE_ENGINES=1` (no network, no keys). UniFi changes: `tests/unifi_test.py` and `tests/unifi_browser_test.js` too.
- **Listener screens are in the listener's language.** No English on a guest's screen after they pick a language. Add a word to `i18n.js` for en, es, fr and sw at least.

## Data

Beside `--data` (installed: `/var/lib/ctranslate`), under the global `LOCK`, each written to a temp file and `os.replace`d, mode 0600: `users.json`, `meetings.json`, `sessions.json` (sha256 of the cookie token), `settings.json` (holds API keys; never returned to a browser), `transcripts/<meeting>.jsonl`, `admin-pin.txt` (first run only), `certs/`.

## Auth

Cookie `vm` (HttpOnly, SameSite=Lax, Secure over TLS or the tunnel). Every write must carry the `X-VM: 1` header (CSRF). Roles: admin, speaker, listener; guests are sessions bound to one meeting, created from its code (no name needed). `is_host()` gates meeting control; `can_join()` gates the stream. Sign-in and code lookups are rate-limited per address (`CF-Connecting-IP` only on the tunnel listener).

## Meetings

`status`: scheduled → live → ended. `access`: invited | staff | anyone (QR guests). `standing`: ending returns it to scheduled, and the code is permanent. `onsite`: guests must be on the LAN listener or come through the tunnel from one of `settings.siteIps`. Phones on the factory Wi-Fi reach the public tunnel name too, so the listener port alone can't tell. `newcode` retires a code and drops its guest sessions.

## Meeting Wi-Fi (UniFi)

- UniFi Network's Integration API: `https://<console>/proxy/network/integration/v1/...`, header `X-API-KEY`, pages of `{offset, limit, count, totalCount, data}`. Needs 10.0.162 or later (`UNIFI_MIN`); `wifi_payload()` adds the fields later versions require (10.1: `advertiseDeviceName`; 10.6.106: the two 2.4 GHz lock fields). The required fields per version come from the official OpenAPI spec; `tests/unifi_test.py` checks each body against them.
- Settings: `unifiUrl`, `unifiKey` (a secret, or `UNIFI_API_KEY`), `unifiSite`, `unifiNetwork` ('' = default), `unifiAps` ([] = every access point), `unifiFingerprint`, `unifiOwned`. `unifi_on()` needs URL, key, site and fingerprint. A new URL clears the fingerprint.
- **Certificate pinning.** Consoles are self-signed. TEST UNIFI (`POST /api/settings/unifi-test`, admin) pins the SHA-256 of the certificate seen; `unifi_call()` compares it after the TLS handshake and **before** the key is sent. A mismatch is an error, never a silent re-trust.
- A meeting's `wifi`, `wifiName` (`clean_ssid()`, default "CT " + title), `wifiPass` (12 characters, new on NEW CODE), and the reconciler's `wifiId`, `wifiApplied`, `wifiState` (`off|up|error`), `wifiError`, `wifiRetryAt`. Hosts alone see the name, password and `wifiQr` (`WIFI:T:WPA;S:..;P:..;;`).
- `WifiReconciler` (one thread, `WIFI.poke()` after every change that matters, and every 5 minutes): wanted = meetings with Wi-Fi that are live. Each pass verifies wanted networks exist (GET; recreated if deleted by hand), deletes unwanted ones, deletes ids in `unifiOwned` that no meeting holds (left over across a restart), and creates what is missing. It deletes only ids in `unifiOwned`. Errors land on the meeting (`wifiError`, retried after `RETRY` or TRY AGAIN: `POST /api/meetings/<id>/wifi`).
- Adding or removing an SSID reprovisions the APs that broadcast it (a few seconds' drop): say so wherever it is offered.

## Test accounts and home testing

- `--demo` adds (or resets) `DEMO_PEOPLE` (invented names, known PINs, `demo: true`) and two demo meetings (`demo: 'room'|'invited'`), writes `demo-accounts.txt`, logs the sheet, then serves. `--demo-setup add|remove` does it (or `remove_demo()`) and exits; the setup menu's **Test accounts** runs it as the service user with the service stopped. Admin → People marks `demo` accounts.
- `GET /api/config` carries `lanUrl` when the page was opened at localhost/127.0.0.1/::1 on the LAN listener: this machine's LAN address and port (`lan_ip()`). `VM.origin()` / `VM.joinLink()` use it, so a speaker at `localhost` (where browsers allow the mic over http) still prints QR codes phones can reach. `publicUrl` wins over both.

## The live pipeline (`Live` in server.py)

- One `Live` per live meeting: connected `Client`s (each with a language and a queue), recent phrases (`segs`, 300), and one worker thread that processes finished phrases **in order**.
- Finished phrase → `translate_many()` into the languages currently connected (Claude: one call, every language, JSON schema; then Google Cloud; then the free endpoint; never raises) → broadcast `seg` → `synthesize()` per language in parallel on `POOL` → `audio` event per language.
- Words still being spoken → `interim()` → `live` events, translated text-only with `translate_fast()` (never Claude). Generation counters drop stale work; the speaker numbers posts (`n`), and live words that arrive after their phrase was finished are dropped. Keep these guards.
- Clients reconnect with `Last-Event-ID`/`since`; `history_for()` replays what they missed, translating languages that weren't connected at the time.
- Voice chain (`synthesize`): Google (voice family picked from the voice list, cached a day; a voice of the same language under another region code when the exact code has none; `None` when Google has no voice for the language) → Azure → free Google Translate read-aloud → `None`, meaning the phone's own `speechSynthesis` reads it.

## Client audio (`Player` in app.js)

One `<audio>` element plays clips in phrase-id order; it loops a second of generated silence between them so the phone keeps the audio session (and the page) alive. `unlock()` must run inside a tap (iPhone). A clip still `pending` is waited for up to 7 s, then the device voice takes it. Over 6 queued or older than 45 s are dropped, and 2+ queued plays at 1.15×. Speakers' playback is suspended while their mic is open (no feedback).

## Speaker recognizers (speak.html)

- `DeviceRec`: the browser's `SpeechRecognition`, with the VTRANSLATE segmenter: every 2 s, anything but the last 4 words (6 characters for zh/ja) is sent as finished once 12 words (24 characters) are pending. `stop()` sends what was on screen, then `abort()`s, so no duplicate final arrives.
- `ServerRec`: `getUserMedia` (any mic) → 16 kHz PCM → simple voice activity detection (adaptive noise floor, 650 ms pause or 12 s cap) → `POST /api/meetings/<id>/audio` → Google STT.
- Mic modes: `lock` (default: tap starts, a 1.5 s hold with a filling ring stops; the release after a stop is ignored), `tap`, `hold`.

## Claude

Raw HTTPS to `/v1/messages` (stdlib, no SDK, by the house rule), `anthropic-version: 2026-10-21`, structured output via `output_config.format` (`json_schema`, one string property per target language), `effort` from settings (default `low`). Default model `claude-haiku-5-5`. Sonnet/Opus get `fallbacks: "default"` with the `server-side-fallback-2026-07-01` beta header (Haiku has no server-side fallback). A `refusal` stop reason falls through to Google. No `temperature` (rejected on this API version).

## Working style

Be direct about tradeoffs and limits. Don't add unrequested features; offer them. Wes deploys by pulling and re-running `install.sh`.
