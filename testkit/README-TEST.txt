CTRANSLATE - TEST AT HOME
=========================

A test server on your own PC, with invented test accounts, for trying CTRANSLATE on your
home Wi-Fi. Plain http: for testing only, not for the factory (the real server is the
ISO installer or install.sh; see README.md).


1. START IT
-----------
Windows: install Python 3 from https://www.python.org/downloads/ (tick "Add python.exe
to PATH"), then double-click START-TEST.bat in this folder.
Mac or Linux: run   sh start-test.sh   in this folder.

The first time, Windows Firewall asks about Python: allow it on Private networks, and
check that your home Wi-Fi is set as a Private network (Settings > Network > Wi-Fi >
your network > Private), or phones cannot reach the PC.

The window lists the addresses and the admin page opens. Closing the window stops the
server. Everything it keeps is in the data folder here; delete it to start over.


2. TEST ACCOUNTS (invented; anyone who reads this can sign in with them)
------------------------------------------------------------------------
  Name               Role       Language   PIN
  Test Admin         admin      English    111111
  Test Speaker       speaker    English    222222
  Test Speaker ES    speaker    Spanish    333333
  Test Listener ES   listener   Spanish    444444
  Test Listener FR   listener   French     555555
  Test Listener SW   listener   Swahili    666666

There is also an "Admin" account; its PIN is in data\admin-pin.txt.
Two meetings are ready:
  Demo training room    a standing QR room run by Test Speaker and Test Speaker ES
  Demo invited meeting  tomorrow 9:00, Test Speaker with the three listeners
Each start puts the test PINs back as above. Admin > People marks them TEST ACCOUNT.


3. TRY IT
---------
a. On the PC, in Chrome or Edge: http://localhost:8080/speak
   Sign in as Test Speaker / 222222, allow the microphone.
   Open "Demo training room" and press START (or make an INSTANT QR MEETING).
   Use localhost on the PC itself: browsers allow the microphone over plain http only
   there. The QR codes still point at the PC's Wi-Fi address, so phones can scan them.

b. On a phone on the same Wi-Fi: scan the QR code (QR button, or PRINT QR SHEET), or open
   http://<PC address>:8080/listen and type the code. Pick Español and tap ESCUCHAR.
   The PC address is the one START-TEST lists (usually 192.168.x.x).

c. Tap the microphone on the PC and talk. The phone shows the Spanish and speaks it.
   Tap starts the microphone; holding it 1.5 s stops it.

d. Invited meetings: on a phone, open /listen, pick Français, tap "Connexion du
   personnel" (staff sign-in) and sign in as Test Listener FR / 555555.
   "Demo invited meeting" is in the list.

Without API keys, translation and voices use Google's free endpoints (unofficial, fine
for a test). For Claude translation and the Google Cloud voices: Admin > Settings >
API keys, SAVE SETTINGS, then TEST ENGINES and the PLAY buttons under Hear each language.


4. PHONES AS SPEAKERS, AND INSTALLING THE APPS (needs https)
------------------------------------------------------------
Phones only allow the microphone, and pinning the apps, over https. A Cloudflare quick
tunnel gives the test server a temporary https address, no account needed:
  Windows:  winget install Cloudflare.cloudflared      Mac:  brew install cloudflared
  then, while START-TEST runs:   cloudflared tunnel --url http://localhost:8081
It prints https://<random words>.trycloudflare.com. Put that address in Admin > Settings
> Public address and SAVE, so QR codes and links use it. The name changes each time
cloudflared starts. Port 8081 is the server's tunnel port: it is open to this PC only.


5. UNIFI MEETING WI-FI
----------------------
Admin > Settings > UniFi meeting Wi-Fi (needs UniFi Network 10.0.162 or later):
  - Console address: your UniFi gateway or console, e.g. https://192.168.1.1
  - API key: UniFi Network > Settings > Control Plane > Integrations > Create API Key
  - TEST UNIFI, then pick the site, the network (VLAN) and the access points, and SAVE.
Then tick "Make a temporary Wi-Fi network for it (UniFi)" when you make a meeting. While
the meeting is live, UniFi broadcasts "CT <title>" with its own password; ending it removes
the network. The QR sheet gets two codes: 1 joins the Wi-Fi, 2 opens the meeting.

At home, two things to know:
  - Phones on that network must reach the PC. A guest network or an isolated VLAN cannot
    reach http://<PC address>:8080. Pick a network that can, or use the quick tunnel
    above (its https address works from any network with internet).
  - Adding or removing a network restarts the radios of the access points that broadcast
    it: their Wi-Fi drops for a few seconds. Pick one access point to keep that off the
    rest of the house.


6. STOPPING, AND STARTING OVER
------------------------------
Close the window (or Ctrl+C). The data folder keeps meetings, settings and keys between
starts; delete it to start clean. API keys typed in Admin are stored in data\settings.json.
