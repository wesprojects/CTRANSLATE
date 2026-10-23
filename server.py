#!/usr/bin/env python3
"""
CTRANSLATE server: live translated meetings. Standard library only, no pip installs.

  python3 server.py --http --port 8080                         # local testing (http://localhost)
  python3 server.py --port 8453 --cert certs/server.crt --key certs/server.key \
                    --tunnel-port 8454                          # production (install.sh)

A speaker talks in their own language. Their phone or PC turns speech into
text (the browser's own recognizer, or Google Speech-to-Text through this
server), the server translates each phrase once per listening language
(free Google endpoint, Google Cloud Translation, or Claude) and voices it
(Google Cloud Text-to-Speech, or the listener's own device voice), and every
listener hears and reads it in their language.

Serves:
  /                    sign in, join with a code      -> public/index.html
  /listen /speak /admin  the three pinnable apps       -> public/<app>.html
  /j/<code>            a meeting's join link          -> /listen?code=<code>
  /api/...             JSON API (see Handler.route), session cookie 'vm'
  /api/meetings/<id>/events   the live stream (Server-Sent Events)

Listeners:
  --port (TLS)         phones and PCs on the LAN, and a Let's Encrypt name
  --tunnel-port        plain http on 127.0.0.1 only, for cloudflared

Data files live in --data (default: beside this file): users.json,
meetings.json, settings.json (API keys; mode 0600), sessions.json,
transcripts/. Each is written to a temporary file and moved into place.
"""
import argparse, base64, collections, concurrent.futures, hashlib, hmac, ipaddress, json
import os, queue, re, secrets, socket, ssl, sys, threading, time, uuid
import urllib.error, urllib.parse, urllib.request
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler

BUILD_VERSION = '2026-10-23.1'   # format: YYYY-MM-DD.N, bump every revision; pages show it

ROOT = os.path.dirname(os.path.abspath(__file__))
PUBLIC = os.path.join(ROOT, 'public')
DATA = ROOT                      # replaced from --data in main()
LOCK = threading.RLock()         # guards every store below and the files behind them

MAX_JSON_BODY = 1 << 20          # 1 MiB; every JSON body here is a few KB
MAX_AUDIO_BODY = 4 << 20         # 16 kHz mono PCM: ~2 minutes; phrases are capped at 15 s
SESSION_DAYS = 180               # a pinned app signs in once
GUEST_HOURS = 24
FAKE = os.environ.get('CTRANSLATE_FAKE_ENGINES') == '1'   # tests and offline demos: no network calls

# ---------------------------------------------------------------------------
# Languages. One entry per language; add one line to add a language.
#   id   our code, stored on people and meetings
#   sr   speech recognition (browser and Google STT), BCP-47
#   tts  Google Text-to-Speech languageCode ('' = none: device voice or text only)
#   mt   Google Translate code
# ---------------------------------------------------------------------------
LANGUAGES = [
    # id    sr        tts       mt       label              tag   color
    ('en', 'en-US', 'en-US', 'en',    'English',          'EN', '#5ecf8d'),
    ('es', 'es-US', 'es-US', 'es',    'Español',          'ES', '#f5a524'),
    ('fr', 'fr-FR', 'fr-FR', 'fr',    'Français',         'FR', '#78aaff'),
    ('sw', 'sw-KE', 'sw-KE', 'sw',    'Kiswahili',        'SW', '#b48edc'),
    ('pl', 'pl-PL', 'pl-PL', 'pl',    'Polski',           'PL', '#e58fa2'),
    ('de', 'de-DE', 'de-DE', 'de',    'Deutsch',          'DE', '#b5c46e'),
    ('zh', 'zh-CN', 'cmn-CN', 'zh-CN', '中文',             'ZH', '#6fc7ce'),
    ('pt', 'pt-BR', 'pt-BR', 'pt',    'Português',        'PT', '#e0a46c'),
    ('vi', 'vi-VN', 'vi-VN', 'vi',    'Tiếng Việt',       'VI', '#d58ad8'),
    ('ar', 'ar-SA', 'ar-XA', 'ar',    'العربية',          'AR', '#8fd4a8'),
    ('hi', 'hi-IN', 'hi-IN', 'hi',    'हिन्दी',            'HI', '#f08a5d'),
    ('ru', 'ru-RU', 'ru-RU', 'ru',    'Русский',          'RU', '#9fb4ff'),
    ('uk', 'uk-UA', 'uk-UA', 'uk',    'Українська',       'UK', '#ffd166'),
    ('ko', 'ko-KR', 'ko-KR', 'ko',    '한국어',            'KO', '#7ad0e8'),
    ('ja', 'ja-JP', 'ja-JP', 'ja',    '日本語',            'JA', '#ff8fab'),
    ('it', 'it-IT', 'it-IT', 'it',    'Italiano',         'IT', '#a3d977'),
    ('tl', 'fil-PH', 'fil-PH', 'tl',  'Tagalog',          'TL', '#c9a0ff'),
    ('ro', 'ro-RO', 'ro-RO', 'ro',    'Română',           'RO', '#f6bd60'),
]
LANG = {l[0]: dict(id=l[0], sr=l[1], tts=l[2], mt=l[3], label=l[4], tag=l[5], color=l[6])
        for l in LANGUAGES}
DEFAULT_LANGS = ['en', 'es', 'fr', 'sw']

# Haiku first: a phrase is short, the call is on the critical path, and it costs
# about $0.15 a meeting-hour at four languages. The larger models are a setting.
CLAUDE_MODELS = ['claude-haiku-5-5', 'claude-sonnet-5-5', 'claude-opus-5-5']
VOICE_TIERS = ('wavenet', 'neural2', 'chirp3-hd', 'standard')
DEFAULT_SETTINGS = {
    'orgName': 'Conklin Office Furniture',
    'publicUrl': '',              # e.g. https://meet.example.com, used for join links and QR codes
    'languages': DEFAULT_LANGS,   # offered in every language picker
    'mtEngine': 'claude',         # claude | google | free; each falls back to the next
    'ttsEngine': 'google',        # google | device | off; google falls back to the device voice
    'serverStt': False,           # allow speakers to use Google Speech-to-Text (needs the Google key)
    'liveCaptions': True,         # translate the words still being spoken, text only
    'claudeModel': 'claude-haiku-5-5',
    'claudeEffort': 'low',
    'glossary': '',               # names and terms Claude should keep or translate a set way
    'voiceGender': 'FEMALE',
    'voiceTier': 'wavenet',       # Google voice family: wavenet | neural2 | chirp3-hd | standard
    'voices': {},                 # optional Google voice name per language id
    'speakingRate': 1.0,
    'transcriptDays': 30,
    'guestsAllowed': True,
    'azureRegion': 'eastus',      # Azure Speech resource region, when an Azure key is set
    'freeVoice': True,            # last resort before the device voice: Google Translate's read-aloud (unofficial)
    'siteIps': [],                # the factory's public internet address(es), for "guests on the Wi-Fi only"
}
SECRET_KEYS = ('googleKey', 'anthropicKey', 'azureKey')
ENV_KEYS = {'googleKey': 'GOOGLE_API_KEY', 'anthropicKey': 'ANTHROPIC_API_KEY', 'azureKey': 'AZURE_SPEECH_KEY'}

# Microsoft Azure neural voices: the backup when Google has no voice for a language or fails.
AZURE_VOICES = {
    'sw': ('sw-KE', 'sw-KE-ZuriNeural', 'sw-KE-RafikiNeural'),
    'es': ('es-US', 'es-US-PalomaNeural', 'es-US-AlonsoNeural'),
    'fr': ('fr-FR', 'fr-FR-DeniseNeural', 'fr-FR-HenriNeural'),
    'en': ('en-US', 'en-US-JennyNeural', 'en-US-GuyNeural'),
    'pl': ('pl-PL', 'pl-PL-ZofiaNeural', 'pl-PL-MarekNeural'),
    'de': ('de-DE', 'de-DE-KatjaNeural', 'de-DE-ConradNeural'),
    'zh': ('zh-CN', 'zh-CN-XiaoxiaoNeural', 'zh-CN-YunxiNeural'),
    'pt': ('pt-BR', 'pt-BR-FranciscaNeural', 'pt-BR-AntonioNeural'),
    'vi': ('vi-VN', 'vi-VN-HoaiMyNeural', 'vi-VN-NamMinhNeural'),
    'ar': ('ar-SA', 'ar-SA-ZariyahNeural', 'ar-SA-HamedNeural'),
    'hi': ('hi-IN', 'hi-IN-SwaraNeural', 'hi-IN-MadhurNeural'),
    'ru': ('ru-RU', 'ru-RU-SvetlanaNeural', 'ru-RU-DmitryNeural'),
    'uk': ('uk-UA', 'uk-UA-PolinaNeural', 'uk-UA-OstapNeural'),
    'ko': ('ko-KR', 'ko-KR-SunHiNeural', 'ko-KR-InJoonNeural'),
    'ja': ('ja-JP', 'ja-JP-NanamiNeural', 'ja-JP-KeitaNeural'),
    'it': ('it-IT', 'it-IT-ElsaNeural', 'it-IT-DiegoNeural'),
    'tl': ('fil-PH', 'fil-PH-BlessicaNeural', 'fil-PH-AngeloNeural'),
    'ro': ('ro-RO', 'ro-RO-AlinaNeural', 'ro-RO-EmilNeural'),
}


def log(*a):
    print(time.strftime('%Y-%m-%d %H:%M:%S'), *a, file=sys.stderr, flush=True)


def now_ms():
    return int(time.time() * 1000)


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------
def path(name):
    return os.path.join(DATA, name)


def load_json(name, default):
    try:
        with open(path(name), encoding='utf-8') as f:
            return json.load(f)
    except FileNotFoundError:
        return default
    except (OSError, ValueError) as e:
        log('!! cannot read', name, e)
        return default


def save_json(name, obj, mode=0o600):
    p = path(name)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    tmp = p + '.tmp'
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    with os.fdopen(fd, 'w', encoding='utf-8') as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
    os.replace(tmp, p)


class Store:
    """Everything persistent, in memory, written through on change. Use under LOCK."""

    def __init__(self):
        self.users = load_json('users.json', {})        # id -> user
        self.meetings = load_json('meetings.json', {})  # id -> meeting
        self.sessions = load_json('sessions.json', {})  # sha256(token) -> session
        s = load_json('settings.json', {})
        self.settings = dict(DEFAULT_SETTINGS, **s)

    def save_users(self):
        save_json('users.json', self.users)

    def save_meetings(self):
        save_json('meetings.json', self.meetings)

    def save_sessions(self):
        save_json('sessions.json', self.sessions)

    def save_settings(self):
        save_json('settings.json', self.settings)


STORE = None


def setting(k):
    return STORE.settings.get(k, DEFAULT_SETTINGS.get(k))


def google_key():
    return setting('googleKey') or os.environ.get('GOOGLE_API_KEY', '')


def anthropic_key():
    return setting('anthropicKey') or os.environ.get('ANTHROPIC_API_KEY', '')


def azure_key():
    return setting('azureKey') or os.environ.get('AZURE_SPEECH_KEY', '')


def enabled_langs():
    return [c for c in setting('languages') if c in LANG] or ['en']


# ---------------------------------------------------------------------------
# People and sessions
# ---------------------------------------------------------------------------
ROLES = ('admin', 'speaker', 'listener')


def hash_pin(pin, salt):
    return hashlib.pbkdf2_hmac('sha256', pin.encode(), bytes.fromhex(salt), 120000).hex()


def set_pin(user, pin):
    user['salt'] = secrets.token_hex(16)
    user['pin'] = hash_pin(pin, user['salt'])


def check_pin(user, pin):
    if not user.get('pin'):
        return False
    return hmac.compare_digest(user['pin'], hash_pin(pin, user['salt']))


def public_user(u, full=False):
    d = {'id': u['id'], 'name': u['name'], 'role': u['role'], 'lang': u.get('lang', 'en')}
    if full:
        d['active'] = u.get('active', True)
        d['created'] = u.get('created')
        d['lastSeen'] = u.get('lastSeen')
    return d


def find_user_by_name(name):
    n = name.strip().casefold()
    for u in STORE.users.values():
        if u['name'].casefold() == n:
            return u
    return None


def clean_name(s, limit=60):
    s = re.sub(r'\s+', ' ', str(s or '')).strip()
    return s[:limit]


def ensure_admin():
    """First run: one admin with a random PIN, printed and written to admin-pin.txt."""
    with LOCK:
        if any(u['role'] == 'admin' for u in STORE.users.values()):
            return
        pin = ''.join(secrets.choice('0123456789') for _ in range(8))
        u = {'id': uuid.uuid4().hex[:10], 'name': 'Admin', 'role': 'admin', 'lang': 'en',
             'active': True, 'created': now_ms()}
        set_pin(u, pin)
        STORE.users[u['id']] = u
        STORE.save_users()
        fd = os.open(path('admin-pin.txt'), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, 'w') as f:
            f.write(pin + '\n')
        log('== first run: sign in as "Admin" with PIN', pin, '(also in admin-pin.txt)')


def new_session(data, ttl):
    token = secrets.token_urlsafe(32)
    data = dict(data, exp=now_ms() + ttl * 1000, created=now_ms())
    with LOCK:
        STORE.sessions[hashlib.sha256(token.encode()).hexdigest()] = data
        STORE.save_sessions()
    return token


def drop_session(token):
    with LOCK:
        if STORE.sessions.pop(hashlib.sha256(token.encode()).hexdigest(), None) is not None:
            STORE.save_sessions()


def resolve_session(token):
    """-> 'me' dict: {kind: user|guest, id, name, role, lang, meeting?} or None."""
    if not token:
        return None
    key = hashlib.sha256(token.encode()).hexdigest()
    with LOCK:
        s = STORE.sessions.get(key)
        if not s:
            return None
        if s['exp'] < now_ms():
            STORE.sessions.pop(key, None)
            return None
        if s.get('uid'):
            u = STORE.users.get(s['uid'])
            if not u or not u.get('active', True):
                return None
            if now_ms() - u.get('lastSeen', 0) > 3600000:
                u['lastSeen'] = now_ms()
                STORE.save_users()
            return {'kind': 'user', 'id': u['id'], 'name': u['name'], 'role': u['role'],
                    'lang': u.get('lang', 'en'), 'prefs': u.get('prefs', {})}
        g = s.get('guest')
        if g:
            return {'kind': 'guest', 'id': g['id'], 'name': g['name'], 'role': 'listener',
                    'lang': g['lang'], 'meeting': g['meeting'], 'prefs': {}}
    return None


class RateLimit:
    """Failed sign-ins per address: 10 in 10 minutes, then wait."""

    def __init__(self, n=10, window=600):
        self.n, self.window, self.hits = n, window, {}
        self.lock = threading.Lock()

    def blocked(self, key):
        with self.lock:
            t = time.time()
            h = [x for x in self.hits.get(key, []) if t - x < self.window]
            self.hits[key] = h
            return len(h) >= self.n

    def fail(self, key):
        with self.lock:
            self.hits.setdefault(key, []).append(time.time())


SIGNIN_LIMIT = RateLimit()
CODE_LIMIT = RateLimit(30, 600)


# ---------------------------------------------------------------------------
# Meetings
# ---------------------------------------------------------------------------
CODE_CHARS = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789'   # no 0/O or 1/I


def new_code():
    while True:
        c = ''.join(secrets.choice(CODE_CHARS) for _ in range(6))
        if not any(m.get('code') == c for m in STORE.meetings.values()):
            return c


def meeting_by_code(code):
    code = re.sub(r'[^A-Z0-9]', '', str(code or '').upper())
    for m in STORE.meetings.values():
        if m.get('code') == code:
            return m
    return None


def is_host(me, m):
    if not me or me['kind'] != 'user':
        return False
    if me['role'] == 'admin':
        return True
    return me['role'] == 'speaker' and (me['id'] in m.get('hosts', []) or m.get('createdBy') == me['id'])


def can_join(me, m):
    if not me:
        return False
    if me['kind'] == 'guest':
        return me.get('meeting') == m['id']
    if me['id'] in m.get('kicked', []):
        return False
    if is_host(me, m) or me['id'] in m.get('listeners', []):
        return True
    return m.get('access') in ('staff', 'anyone')


def visible_in_list(me, m):
    if me['kind'] == 'guest':
        return me.get('meeting') == m['id']
    if me['role'] == 'admin':
        return True
    if me['id'] in m.get('hosts', []) or me['id'] in m.get('listeners', []) or m.get('createdBy') == me['id']:
        return True
    return m.get('access') in ('staff', 'anyone')


def meeting_view(m, me):
    names = lambda ids: [public_user(STORE.users[i]) for i in ids if i in STORE.users]
    d = {k: m.get(k) for k in ('id', 'title', 'notes', 'status', 'start', 'duration', 'access',
                               'createdAt', 'startedAt', 'endedAt', 'code', 'standing', 'onsite')}
    d['hosts'] = names(m.get('hosts', []))
    d['listeners'] = names(m.get('listeners', []))
    d['createdBy'] = m.get('createdBy')
    d['canHost'] = is_host(me, m)
    d['canJoin'] = can_join(me, m)
    live = LIVE.get(m['id'])
    d['online'] = live.count() if live else 0
    d['joinUrl'] = join_url(m)
    return d


def join_url(m):
    base = (setting('publicUrl') or '').rstrip('/')
    return (base + '/j/' + m['code']) if base else '/j/' + m['code']


def clean_meeting_fields(body, m, me):
    """Validate a create/update body into meeting m. Returns an error string or None."""
    if 'title' in body:
        t = clean_name(body['title'], 120)
        if not t:
            return 'A meeting needs a title.'
        m['title'] = t
    if 'notes' in body:
        m['notes'] = str(body['notes'] or '')[:2000]
    if 'start' in body:
        s = body['start']
        if s in (None, '', 0):
            m['start'] = None
        elif isinstance(s, (int, float)) and s > 0:
            m['start'] = int(s)
        else:
            return 'start must be a time in milliseconds.'
    if 'duration' in body:
        try:
            m['duration'] = max(5, min(24 * 60, int(body['duration'])))
        except (TypeError, ValueError):
            return 'duration must be minutes.'
    for k in ('standing', 'onsite'):
        if k in body:
            m[k] = bool(body[k])
    if 'access' in body:
        if body['access'] not in ('invited', 'staff', 'anyone'):
            return 'access must be invited, staff or anyone.'
        if body['access'] == 'anyone' and not setting('guestsAllowed'):
            return 'Guests are turned off in settings.'
        m['access'] = body['access']
    for k in ('hosts', 'listeners'):
        if k in body:
            ids = body[k]
            if not isinstance(ids, list):
                return k + ' must be a list.'
            ids = [i for i in dict.fromkeys(ids) if i in STORE.users]
            if k == 'hosts':
                ids = [i for i in ids if STORE.users[i]['role'] in ('speaker', 'admin')]
            m[k] = ids
    if me['role'] == 'speaker' and me['id'] not in m.get('hosts', []):
        m.setdefault('hosts', []).insert(0, me['id'])   # a speaker always hosts what they make
    return None


# ---------------------------------------------------------------------------
# Live meetings: connected clients, the phrase pipeline, the event stream
# ---------------------------------------------------------------------------
LIVE = {}            # meeting id -> Live
LIVE_LOCK = threading.Lock()
POOL = concurrent.futures.ThreadPoolExecutor(max_workers=24, thread_name_prefix='work')
AUDIO = collections.OrderedDict()     # audio id -> (bytes, mime, expires)
AUDIO_LOCK = threading.Lock()
AUDIO_MAX_BYTES = 200 << 20
AUDIO_TTL = 900


def audio_put(data, mime='audio/mpeg'):
    aid = secrets.token_urlsafe(18)
    with AUDIO_LOCK:
        AUDIO[aid] = (data, mime, time.time() + AUDIO_TTL)
        total = sum(len(v[0]) for v in AUDIO.values())
        while total > AUDIO_MAX_BYTES and AUDIO:
            _, v = AUDIO.popitem(last=False)
            total -= len(v[0])
    return '/api/audio/' + aid


def audio_get(aid):
    with AUDIO_LOCK:
        v = AUDIO.get(aid)
        if v and v[2] > time.time():
            return v
    return None


class Client:
    def __init__(self, me, lang, voice):
        self.cid = uuid.uuid4().hex[:12]
        self.uid = me['id']
        self.name = me['name']
        self.role = me['role']
        self.guest = me['kind'] == 'guest'
        self.lang = lang
        self.voice = voice
        self.q = queue.Queue(maxsize=1000)
        self.joined = now_ms()
        self.closed = False

    def send(self, event, data, eid=None):
        if self.closed:
            return
        try:
            self.q.put_nowait((event, data, eid))
        except queue.Full:       # a client that stopped reading: cut it loose, it reconnects
            self.close()

    def close(self):
        self.closed = True
        try:
            self.q.put_nowait(None)
        except queue.Full:
            pass

    def view(self):
        return {'cid': self.cid, 'uid': self.uid, 'name': self.name, 'role': self.role,
                'guest': self.guest, 'lang': self.lang, 'voice': self.voice, 'joined': self.joined}


class Live:
    """One live meeting: its clients, its recent phrases and its ordered phrase worker."""

    def __init__(self, mid):
        self.mid = mid
        self.clients = {}
        self.lock = threading.Lock()
        self.segs = collections.deque(maxlen=300)
        self.seq = 0
        self.jobs = queue.Queue()
        self.interim_gen = {}          # speaker uid -> generation, drops stale interim work
        self.final_n = {}              # speaker uid -> sequence number of their last finished phrase
        self.context = collections.deque(maxlen=4)   # recent source phrases, for Claude
        self.last_speech = time.time()
        self.worker = threading.Thread(target=self.run, name='meeting-' + mid, daemon=True)
        self.worker.start()

    def count(self):
        with self.lock:
            return len(self.clients)

    def add(self, c):
        with self.lock:
            self.clients[c.cid] = c
        self.roster()

    def remove(self, cid):
        with self.lock:
            c = self.clients.pop(cid, None)
        if c:
            c.close()
            self.roster()

    def all(self):
        with self.lock:
            return list(self.clients.values())

    def roster(self):
        people = [c.view() for c in self.all()]
        for c in self.all():
            c.send('roster', {'people': people})

    def langs(self):
        return {c.lang for c in self.all()}

    def broadcast(self, event, data):
        for c in self.all():
            c.send(event, data)

    def close_all(self, event=None, data=None):
        for c in self.all():
            if event:
                c.send(event, data or {})
            c.close()
        with self.lock:
            self.clients.clear()

    # ---- phrases ----
    def submit(self, speaker, lang, text):
        self.last_speech = time.time()
        self.interim_gen[speaker['id']] = self.interim_gen.get(speaker['id'], 0) + 1
        self.jobs.put((speaker, lang, text))

    def run(self):
        while True:
            job = self.jobs.get()
            if job is None:
                return
            try:
                self.process(*job)
            except Exception as e:      # one bad phrase must never stop the meeting
                log('!! phrase failed', self.mid, repr(e))

    def process(self, speaker, src, text):
        targets = sorted(self.langs() - {src})
        tr = translate_many(text, src, targets, list(self.context)) if targets else {}
        self.context.append(text)
        with self.lock:
            self.seq += 1
            seg = {'id': self.seq, 't': now_ms(), 'speaker': {'id': speaker['id'], 'name': speaker['name']},
                   'lang': src, 'src': text, 'tr': tr, 'audio': {}}
            self.segs.append(seg)
        record_transcript(self.mid, seg)
        for c in self.all():
            c.send('seg', seg_for(seg, c.lang), seg['id'])
        # Voices: one synthesis per language, in parallel; each listener queues by phrase id.
        if server_voice_on():
            for lang in targets:
                if tr.get(lang):
                    POOL.submit(self.voice_one, seg, lang)

    def voice_one(self, seg, lang):
        mp3 = synthesize(seg['tr'][lang], lang)
        url = audio_put(mp3) if mp3 else None
        seg['audio'][lang] = url
        for c in self.all():
            if c.lang == lang:
                c.send('audio', {'id': seg['id'], 'url': url})

    def interim(self, speaker, src, text, n=0):
        """Words still being spoken: shown live, translated text-only on the cheap engine.
        n orders a speaker's posts: words that arrive after the phrase they belong to was
        finished are dropped, so a finished phrase is never followed by a stale live line."""
        if not setting('liveCaptions') or n and n <= self.final_n.get(speaker['id'], 0):
            return
        self.last_speech = time.time()
        gen = self.interim_gen.get(speaker['id'], 0) + 1
        self.interim_gen[speaker['id']] = gen
        who = {'id': speaker['id'], 'name': speaker['name']}
        for c in self.all():
            if c.lang == src:
                c.send('live', {'speaker': who, 'text': text, 'lang': src})
        for lang in self.langs() - {src}:
            POOL.submit(self._interim_one, speaker['id'], gen, who, src, lang, text)

    def _interim_one(self, uid, gen, who, src, lang, text):
        if self.interim_gen.get(uid) != gen:
            return
        try:
            out = translate_fast(text, src, lang)
        except Exception:
            return
        if out and self.interim_gen.get(uid) == gen:
            for c in self.all():
                if c.lang == lang:
                    c.send('live', {'speaker': who, 'text': out, 'lang': lang})

    def clear_interim(self, speaker):
        self.interim_gen[speaker['id']] = self.interim_gen.get(speaker['id'], 0) + 1
        self.broadcast('live', {'speaker': {'id': speaker['id'], 'name': speaker['name']}, 'text': ''})

    def history_for(self, lang, since=0, limit=30):
        """Recent phrases for a client joining or reconnecting; fills missing languages."""
        with self.lock:
            segs = [s for s in self.segs if s['id'] > since][-limit:]
        need = [s for s in segs if s['lang'] != lang and lang not in s['tr']]
        if need:
            futs = {POOL.submit(translate_many, s['src'], s['lang'], [lang], []): s for s in need}
            for f, s in futs.items():
                try:
                    s['tr'].update(f.result(timeout=20))
                except Exception:
                    pass
        return [seg_for(s, lang) for s in segs]

    def stop(self):
        self.jobs.put(None)


def seg_for(seg, lang):
    same = seg['lang'] == lang
    text = seg['src'] if same else seg['tr'].get(lang)
    return {'id': seg['id'], 't': seg['t'], 'speaker': seg['speaker'], 'lang': seg['lang'],
            'src': seg['src'], 'text': text, 'same': same, 'tr': seg['tr'],
            'audio': None if same else seg['audio'].get(lang, 'pending' if server_voice_on() else None)}


def server_voice_on():
    """Phrases are voiced on the server: Google, else Azure, else the free read-aloud."""
    return setting('ttsEngine') == 'google' and bool(google_key() or azure_key() or setting('freeVoice'))


def live_for(mid, create=False):
    with LIVE_LOCK:
        lv = LIVE.get(mid)
        if not lv and create:
            lv = LIVE[mid] = Live(mid)
        return lv


def end_live(mid, reason='ended'):
    with LIVE_LOCK:
        lv = LIVE.pop(mid, None)
    if lv:
        lv.close_all('status', {'status': reason})
        lv.stop()


def record_transcript(mid, seg):
    try:
        p = path(os.path.join('transcripts', mid + '.jsonl'))
        os.makedirs(os.path.dirname(p), exist_ok=True)
        line = json.dumps({'t': seg['t'], 'speaker': seg['speaker']['name'], 'lang': seg['lang'],
                           'src': seg['src'], 'tr': seg['tr']}, ensure_ascii=False)
        with LOCK:
            fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            with os.fdopen(fd, 'a', encoding='utf-8') as f:
                f.write(line + '\n')
    except OSError as e:
        log('!! transcript', e)


def transcript_text(m):
    p = path(os.path.join('transcripts', m['id'] + '.jsonl'))
    lines = ['CTRANSLATE - MEETING TRANSCRIPT', 'Meeting: ' + m['title'],
             'Started: ' + (time.strftime('%Y-%m-%d %H:%M', time.localtime(m['startedAt'] / 1000))
                            if m.get('startedAt') else '-'),
             'Build: ' + BUILD_VERSION, '=' * 60]
    try:
        with open(p, encoding='utf-8') as f:
            for raw in f:
                try:
                    e = json.loads(raw)
                except ValueError:
                    continue
                lines.append('')
                lines.append('[%s] %s (%s)' % (time.strftime('%H:%M:%S', time.localtime(e['t'] / 1000)),
                                              e['speaker'], LANG.get(e['lang'], {}).get('tag', e['lang'])))
                lines.append('  %s: %s' % (LANG.get(e['lang'], {}).get('tag', e['lang']), e['src']))
                for k, v in sorted(e.get('tr', {}).items()):
                    lines.append('  %s: %s' % (LANG.get(k, {}).get('tag', k), v or '(unavailable)'))
    except FileNotFoundError:
        lines.append('')
        lines.append('(nothing was said)')
    return '\n'.join(lines) + '\n'


# ---------------------------------------------------------------------------
# Engines: translation, voices, speech recognition
# ---------------------------------------------------------------------------
def http_json(url, body=None, headers=None, timeout=12):
    data = json.dumps(body).encode() if body is not None else None
    h = {'Content-Type': 'application/json', 'User-Agent': 'ctranslate/' + BUILD_VERSION}
    h.update(headers or {})
    req = urllib.request.Request(url, data=data, headers=h, method='POST' if data else 'GET')
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode('utf-8'))
    except urllib.error.HTTPError as e:
        detail = e.read().decode('utf-8', 'replace')[:400]
        raise RuntimeError('HTTP %s: %s' % (e.code, detail)) from None


def translate_free(text, src, dst):
    """Google's public gtx endpoint (no key; what VTRANSLATE uses). MyMemory if it fails."""
    if FAKE:
        return '[%s] %s' % (dst, text)
    s, d = LANG[src]['mt'], LANG[dst]['mt']
    try:
        u = ('https://translate.googleapis.com/translate_a/single?client=gtx&dt=t&sl=%s&tl=%s&q=%s'
             % (s, d, urllib.parse.quote(text)))
        r = http_json(u, timeout=8)
        return ''.join(seg[0] for seg in r[0] if seg and seg[0])
    except Exception:
        u = ('https://api.mymemory.translated.net/get?q=%s&langpair=%s|%s'
             % (urllib.parse.quote(text), s.split('-')[0], d.split('-')[0]))
        r = http_json(u, timeout=8)
        return (r.get('responseData') or {}).get('translatedText') or None


def translate_google(text, src, dst):
    """Google Cloud Translation v2 (API key)."""
    if FAKE:
        return '[%s] %s' % (dst, text)
    r = http_json('https://translation.googleapis.com/language/translate/v2?key=' + urllib.parse.quote(google_key()),
                  {'q': [text], 'source': LANG[src]['mt'], 'target': LANG[dst]['mt'], 'format': 'text'})
    import html as _html
    return _html.unescape(r['data']['translations'][0]['translatedText'])


def translate_claude(text, src, targets, context):
    """One Claude call per phrase, every target language at once, as JSON."""
    if FAKE:
        return {t: '[claude %s] %s' % (t, text) for t in targets}
    names = {t: LANG[t]['label'] + ' (' + LANG[t]['mt'] + ')' for t in targets}
    system = (
        'You are a professional live interpreter for spoken workplace meetings at %s. '
        'Translate the speaker\'s latest utterance from %s into each requested language. '
        'Write natural spoken language a listener will hear aloud: faithful, plain, the same register '
        'as the speaker, no added commentary, notes or quotation marks. The utterance comes from speech '
        'recognition and may be a fragment of a longer sentence or contain recognition errors; translate '
        'the fragment as it stands without completing it, and quietly correct an obvious misrecognition '
        'only when the context makes the intended word clear. Keep personal names, product names, model '
        'numbers and figures unchanged. Latency-sensitive; begin your visible answer immediately.'
        % (setting('orgName') or 'a company', LANG[src]['label']))
    gl = (setting('glossary') or '').strip()
    if gl:
        system += '\n\nCompany glossary (follow it):\n' + gl[:4000]
    user = {'earlier_utterances': context[-3:], 'utterance': text,
            'translate_into': names}
    schema = {'type': 'object', 'additionalProperties': False, 'required': list(targets),
              'properties': {t: {'type': 'string'} for t in targets}}
    body = {
        'model': setting('claudeModel') if setting('claudeModel') in CLAUDE_MODELS else CLAUDE_MODELS[0],
        'max_tokens': 4000,
        'system': system,
        'messages': [{'role': 'user', 'content': json.dumps(user, ensure_ascii=False)}],
        'output_config': {'effort': setting('claudeEffort') or 'low',
                          'format': {'type': 'json_schema', 'schema': schema}},
    }
    headers = {'x-api-key': anthropic_key(), 'anthropic-version': '2026-10-21'}
    if body['model'] != 'claude-haiku-5-5':     # Haiku has no server-side fallback
        body['fallbacks'] = 'default'
        headers['anthropic-beta'] = 'server-side-fallback-2026-07-01'
    r = http_json('https://api.anthropic.com/v1/messages', body, headers, timeout=20)
    if r.get('stop_reason') == 'refusal':
        raise RuntimeError('Claude declined this phrase')
    txt = ''.join(b.get('text', '') for b in r.get('content', []) if b.get('type') == 'text')
    out = json.loads(txt)
    return {t: str(out.get(t) or '').strip() for t in targets}


def translate_fast(text, src, dst):
    """Live captions: Cloud Translation when a key is set, else the free endpoint. Never Claude."""
    if setting('mtEngine') in ('google', 'claude') and google_key():
        return translate_google(text, src, dst)
    return translate_free(text, src, dst)


def translate_many(text, src, targets, context):
    """Finished phrase -> {lang: text or None}. Falls back engine by engine, never raises."""
    engine = setting('mtEngine')
    out = {}
    if engine == 'claude' and anthropic_key():
        try:
            out = translate_claude(text, src, targets, context)
        except Exception as e:
            log('!! claude translation, falling back:', repr(e))
    missing = [t for t in targets if not out.get(t)]

    def one(t):
        for fn in ((translate_google, translate_free) if google_key() and engine != 'free'
                   else (translate_free,)):
            try:
                r = fn(text, src, t)
                if r:
                    return r
            except Exception as e:
                log('!! translation', fn.__name__, t, repr(e))
        return None
    if missing:
        for t, r in zip(missing, POOL.map(one, missing) if len(missing) > 1 else [one(missing[0])]):
            out[t] = r
    return out


VOICES = {'at': 0, 'list': [], 'picked': {}}
VOICES_LOCK = threading.Lock()


def voice_for(lang):
    """The Google voice to use for a language: the admin's named voice, else the best match
    for the chosen family and gender from Google's voice list (fetched once a day), else
    Google's default for the language code."""
    code = LANG[lang]['tts']
    named = (setting('voices') or {}).get(lang)
    if named:
        return {'languageCode': code, 'name': named}
    tier, gender = setting('voiceTier') or 'wavenet', setting('voiceGender') or 'FEMALE'
    key = (lang, tier, gender)
    with VOICES_LOCK:
        if key in VOICES['picked'] and time.time() - VOICES['at'] < 86400:
            return VOICES['picked'][key]
    if time.time() - VOICES['at'] >= 86400 or not VOICES['list']:
        try:
            vs = http_json('https://texttospeech.googleapis.com/v1/voices?key=' + urllib.parse.quote(google_key()),
                           timeout=8).get('voices', [])
            with VOICES_LOCK:
                VOICES.update(at=time.time(), list=vs, picked={})
        except Exception as e:
            log('!! voice list', repr(e))
    tag = {'wavenet': '-wavenet-', 'neural2': '-neural2-', 'chirp3-hd': '-chirp3-hd-', 'standard': '-standard-'}[tier]
    cands = [v for v in VOICES['list'] if code in v.get('languageCodes', [])]
    if not cands:
        # Same language under another region (Swahili listed as sw-TZ rather than sw-KE, say).
        base = code.split('-')[0].lower()
        cands = [v for v in VOICES['list']
                 if any(c.split('-')[0].lower() == base for c in v.get('languageCodes', []))]
    if VOICES['list'] and not cands:
        with VOICES_LOCK:
            VOICES['picked'][key] = None
        return None                      # Google has no voice for this language
    # Prefer the family, then the gender, then any family that sounds better than Standard.
    rank = lambda v: (tag in v['name'].lower(), v.get('ssmlGender') == gender,
                      '-standard-' not in v['name'].lower())
    best = max(cands, key=rank) if cands else None
    # A voice is asked for with a language code it lists.
    voice = {'languageCode': code if code in best.get('languageCodes', []) else best['languageCodes'][0],
             'name': best['name']} if best else {'languageCode': code, 'ssmlGender': gender}
    with VOICES_LOCK:
        VOICES['picked'][key] = voice
    return voice


def tts_google(text, lang):
    """Google Cloud Text-to-Speech -> MP3 bytes, or None for a language it has no voice for."""
    code = LANG[lang]['tts']
    if not code:
        return None
    if FAKE:
        return b'ID3FAKE' + text.encode()
    voice = voice_for(lang)
    if not voice:
        return None
    audio = {'audioEncoding': 'MP3'}
    if '-chirp3-hd-' not in voice.get('name', '').lower():     # Chirp 3 HD sets its own pace
        audio['speakingRate'] = max(0.5, min(2.0, float(setting('speakingRate') or 1)))
    r = http_json('https://texttospeech.googleapis.com/v1/text:synthesize?key=' + urllib.parse.quote(google_key()),
                  {'input': {'text': text[:4500]}, 'voice': voice, 'audioConfig': audio})
    return base64.b64decode(r['audioContent'])


def tts_azure(text, lang):
    """Microsoft Azure neural voice -> MP3 bytes."""
    v = AZURE_VOICES.get(lang)
    if not v:
        return None
    if FAKE:
        return b'ID3AZURE' + text.encode()
    from xml.sax.saxutils import escape
    name = v[2] if setting('voiceGender') == 'MALE' else v[1]
    rate = int(round((float(setting('speakingRate') or 1) - 1) * 100))
    ssml = ("<speak version='1.0' xml:lang='%s'><voice name='%s'><prosody rate='%+d%%'>%s</prosody></voice></speak>"
            % (v[0], name, rate, escape(text[:3000])))
    req = urllib.request.Request(
        'https://%s.tts.speech.microsoft.com/cognitiveservices/v1' % re.sub(r'[^a-z0-9]', '', setting('azureRegion') or 'eastus'),
        data=ssml.encode('utf-8'), method='POST',
        headers={'Ocp-Apim-Subscription-Key': azure_key(), 'Content-Type': 'application/ssml+xml',
                 'X-Microsoft-OutputFormat': 'audio-24khz-48kbitrate-mono-mp3', 'User-Agent': 'ctranslate'})
    try:
        with urllib.request.urlopen(req, timeout=12) as r:
            return r.read()
    except urllib.error.HTTPError as e:
        raise RuntimeError('HTTP %s: %s' % (e.code, e.read().decode('utf-8', 'replace')[:300])) from None


def tts_free(text, lang):
    """Google Translate's read-aloud: no key, unofficial, about 200 characters a request.
    Long phrases go in pieces; MP3 frames join end to end."""
    if FAKE:
        return b'ID3FREE' + text.encode()
    pieces, cur = [], ''
    for word in text.split():
        if len(cur) + len(word) + 1 > 190 and cur:
            pieces.append(cur)
            cur = ''
        cur = (cur + ' ' + word).strip()
    if cur:
        pieces.append(cur)
    out = b''
    for piece in pieces[:12]:
        u = ('https://translate.google.com/translate_tts?ie=UTF-8&client=tw-ob&tl=%s&q=%s'
             % (LANG[lang]['mt'], urllib.parse.quote(piece)))
        req = urllib.request.Request(u, headers={'User-Agent': 'Mozilla/5.0', 'Referer': 'https://translate.google.com/'})
        with urllib.request.urlopen(req, timeout=8) as r:
            data = r.read()
        if not data.startswith((b'ID3', b'\xff')):
            raise RuntimeError('not audio')
        out += data
    return out or None


def synthesize(text, lang):
    """A phrase's voice, trying each source in turn: Google Cloud, Azure, the free read-aloud.
    None means the listener's own device voice takes it."""
    chain = []
    if google_key():
        chain.append(tts_google)
    if azure_key():
        chain.append(tts_azure)
    if setting('freeVoice'):
        chain.append(tts_free)
    for fn in chain:
        try:
            mp3 = fn(text, lang)
            if mp3:
                return mp3
        except Exception as e:
            log('!! voice', fn.__name__, lang, repr(e))
    return None


def stt_google(pcm, lang):
    """Google Speech-to-Text v1, 16 kHz mono 16-bit PCM -> text."""
    if FAKE:
        return 'spoken phrase of %d bytes' % len(pcm)
    r = http_json('https://speech.googleapis.com/v1/speech:recognize?key=' + urllib.parse.quote(google_key()),
                  {'config': {'encoding': 'LINEAR16', 'sampleRateHertz': 16000,
                              'languageCode': LANG[lang]['sr'], 'enableAutomaticPunctuation': True},
                   'audio': {'content': base64.b64encode(pcm).decode()}}, timeout=20)
    return ' '.join(x['alternatives'][0]['transcript'].strip()
                    for x in r.get('results', []) if x.get('alternatives')).strip()


# ---------------------------------------------------------------------------
# Housekeeping: expired sessions, stale meetings, old transcripts, audio
# ---------------------------------------------------------------------------
def housekeeping():
    while True:
        time.sleep(60)
        try:
            t = now_ms()
            with LOCK:
                gone = [k for k, s in STORE.sessions.items() if s['exp'] < t]
                for k in gone:
                    del STORE.sessions[k]
                if gone:
                    STORE.save_sessions()
                changed = False
                for m in STORE.meetings.values():
                    if m['status'] != 'live':
                        continue
                    lv = LIVE.get(m['id'])
                    idle = time.time() - (lv.last_speech if lv else 0)
                    planned_end = (m.get('startedAt') or t) + (m.get('duration') or 60) * 60000
                    # Ended by itself: 2 hours of silence, or an hour past its planned end with nobody on.
                    if idle > 7200 or (t > planned_end + 3600000 and (not lv or lv.count() == 0)):
                        st = 'scheduled' if m.get('standing') else 'ended'
                        m['status'], m['endedAt'] = st, t
                        changed = True
                        end_live(m['id'], st)
                        log('== meeting ended by itself:', m['title'])
                if changed:
                    STORE.save_meetings()
            keep = max(1, int(setting('transcriptDays') or 30)) * 86400
            tdir = path('transcripts')
            if os.path.isdir(tdir):
                for f in os.listdir(tdir):
                    p = os.path.join(tdir, f)
                    if time.time() - os.path.getmtime(p) > keep:
                        os.remove(p)
            with AUDIO_LOCK:
                for k in [k for k, v in AUDIO.items() if v[2] < time.time()]:
                    del AUDIO[k]
        except Exception as e:
            log('!! housekeeping', repr(e))


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------
STATIC_TYPES = {'.html': 'text/html; charset=utf-8', '.js': 'text/javascript; charset=utf-8',
                '.css': 'text/css; charset=utf-8', '.png': 'image/png', '.svg': 'image/svg+xml',
                '.webmanifest': 'application/manifest+json', '.ico': 'image/x-icon',
                '.json': 'application/json'}
PAGES = {'/': 'index.html', '/listen': 'listen.html', '/speak': 'speak.html', '/admin': 'admin.html',
         '/print': 'print.html'}


class HttpError(Exception):
    def __init__(self, code, msg):
        super().__init__(msg)
        self.code, self.msg = code, msg


class Handler(BaseHTTPRequestHandler):
    server_version = 'ctranslate'
    timeout = 120               # per socket operation; the event stream writes every 15 s
    sys_version = ''

    def log_message(self, fmt, *args):
        pass    # quiet; errors are logged where they happen

    # ---- helpers ----
    @property
    def tunnel(self):
        return getattr(self.server, 'tunnel', False)

    def client_ip(self):
        if self.tunnel:
            return self.headers.get('CF-Connecting-IP') or self.client_address[0]
        return self.client_address[0]

    def onsite(self):
        """On the company network: came in on the LAN listener, or through the tunnel from one of
        the site's own public addresses (phones on the factory Wi-Fi reach the public name too)."""
        if not self.tunnel:
            return True
        try:
            ip = ipaddress.ip_address(self.client_ip())
            return any(ip in ipaddress.ip_network(n, strict=False) for n in setting('siteIps') or [])
        except ValueError:
            return False

    def secure(self):
        return self.tunnel or isinstance(self.connection, ssl.SSLSocket)

    def token(self):
        for part in (self.headers.get('Cookie') or '').split(';'):
            k, _, v = part.strip().partition('=')
            if k == 'vm':
                return v
        return None

    def me(self):
        return resolve_session(self.token())

    def need(self, role=None):
        me = self.me()
        if not me:
            raise HttpError(401, 'Please sign in.')
        if role == 'admin' and not (me['kind'] == 'user' and me['role'] == 'admin'):
            raise HttpError(403, 'Only an admin can do that.')
        if role == 'host' and not (me['kind'] == 'user' and me['role'] in ('admin', 'speaker')):
            raise HttpError(403, 'Only a speaker or admin can do that.')
        return me

    def body_json(self):
        n = int(self.headers.get('Content-Length') or 0)
        if n > MAX_JSON_BODY:
            raise HttpError(413, 'Too large.')
        raw = self.rfile.read(n) if n else b''
        if not raw:
            return {}
        try:
            d = json.loads(raw.decode('utf-8'))
        except ValueError:
            raise HttpError(400, 'Not JSON.')
        if not isinstance(d, dict):
            raise HttpError(400, 'Expected an object.')
        return d

    def headers_common(self):
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'same-origin')
        self.send_header('X-Frame-Options', 'DENY')
        self.send_header('Permissions-Policy', 'microphone=(self), camera=(), geolocation=()')
        if self.secure():
            self.send_header('Strict-Transport-Security', 'max-age=31536000')

    def send_json(self, code, obj, cookie=None):
        data = json.dumps(obj, ensure_ascii=False).encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store')
        if cookie is not None:
            self.send_header('Set-Cookie', cookie)
        self.headers_common()
        self.end_headers()
        self.wfile.write(data)

    def set_cookie(self, token, max_age):
        c = 'vm=%s; Path=/; Max-Age=%d; HttpOnly; SameSite=Lax' % (token, max_age)
        return c + ('; Secure' if self.secure() else '')

    def send_bytes(self, code, data, ctype, cache='no-cache', extra=None):
        self.send_response(code)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', cache)
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.headers_common()
        self.end_headers()
        if self.command != 'HEAD':
            self.wfile.write(data)

    def redirect(self, to):
        self.send_response(302)
        self.send_header('Location', to)
        self.send_header('Content-Length', '0')
        self.end_headers()

    # ---- dispatch ----
    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        self.dispatch('GET')

    def do_POST(self):
        self.dispatch('POST')

    def do_PUT(self):
        self.dispatch('PUT')

    def do_DELETE(self):
        self.dispatch('DELETE')

    def dispatch(self, method):
        url = urllib.parse.urlsplit(self.path)
        p = url.path
        q = dict(urllib.parse.parse_qsl(url.query))
        try:
            if p.startswith('/api/'):
                # Cross-site request defence: every write carries this header, which a form
                # on another site cannot send, and the cookie is SameSite=Lax besides.
                if method != 'GET' and self.headers.get('X-VM') != '1':
                    raise HttpError(403, 'Missing X-VM header.')
                return self.route(method, p, q)
            if method != 'GET':
                raise HttpError(405, 'Method not allowed.')
            return self.static(p)
        except HttpError as e:
            self.send_json(e.code, {'error': e.msg})
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as e:
            log('!! %s %s: %r' % (method, p, e))
            try:
                self.send_json(500, {'error': 'Server error: %s' % e.__class__.__name__})
            except Exception:
                pass

    def static(self, p):
        m = re.match(r'^/j/([A-Za-z0-9]{4,10})/?$', p)
        if m:
            return self.redirect('/listen?code=' + m.group(1).upper())
        if p in ('/listen/', '/speak/', '/admin/'):
            return self.redirect(p[:-1])
        name = PAGES.get(p) or p.lstrip('/')
        full = os.path.normpath(os.path.join(PUBLIC, name))
        if not full.startswith(PUBLIC + os.sep) or not os.path.isfile(full):
            return self.send_bytes(404, b'Not found', 'text/plain; charset=utf-8')
        ext = os.path.splitext(full)[1]
        with open(full, 'rb') as f:
            data = f.read()
        if ext in ('.html', '.js', '.webmanifest', '.css'):
            data = data.replace(b'__BUILD__', BUILD_VERSION.encode())
        extra = {}
        if name == 'sw.js':
            extra['Service-Worker-Allowed'] = '/'
        cache = 'no-cache' if ext in ('.html', '.js', '.css', '.webmanifest') else 'public, max-age=86400'
        self.send_bytes(200, data, STATIC_TYPES.get(ext, 'application/octet-stream'), cache, extra)

    def route(self, method, p, q):
        parts = p.strip('/').split('/')[1:]     # drop 'api'
        head = parts[0] if parts else ''
        r = (method, head)

        # ---- public ----
        if r == ('GET', 'config'):
            return self.send_json(200, {
                'build': BUILD_VERSION, 'orgName': setting('orgName'),
                'languages': [LANG[c] for c in enabled_langs()],
                'allLanguages': [LANG[l[0]] for l in LANGUAGES],
                'ttsEngine': setting('ttsEngine') if (setting('ttsEngine') != 'google' or server_voice_on()) else 'device',
                'serverStt': bool(setting('serverStt') and google_key()),
                'liveCaptions': setting('liveCaptions'), 'guestsAllowed': setting('guestsAllowed'),
                'publicUrl': setting('publicUrl'),
                'onsiteReady': bool(setting('siteIps')) or not TUNNEL_IN_USE[0]})
        if r == ('GET', 'code') and len(parts) == 2:
            ip = self.client_ip()
            if CODE_LIMIT.blocked(ip):
                raise HttpError(429, 'Too many tries. Wait a few minutes.')
            with LOCK:
                m = meeting_by_code(parts[1])
            if not m or m['status'] == 'ended':
                CODE_LIMIT.fail(ip)
                raise HttpError(404, 'No meeting with that code is open.')
            return self.send_json(200, {'id': m['id'], 'title': m['title'], 'status': m['status'],
                                        'start': m.get('start'), 'standing': bool(m.get('standing')),
                                        'guests': m.get('access') == 'anyone' and bool(setting('guestsAllowed'))
                                        and not (m.get('onsite') and not self.onsite()),
                                        'offsite': bool(m.get('onsite') and not self.onsite())})
        if r == ('POST', 'login'):
            return self.login()
        if r == ('POST', 'guest'):
            return self.guest()
        if r == ('POST', 'logout'):
            t = self.token()
            if t:
                drop_session(t)
            return self.send_json(200, {'ok': True}, cookie=self.set_cookie('', 0))
        if r == ('GET', 'audio') and len(parts) == 2:
            v = audio_get(parts[1])
            if not v:
                raise HttpError(404, 'Gone.')
            return self.send_bytes(200, v[0], v[1], 'private, max-age=900')

        # ---- signed in ----
        me = self.need()
        if r == ('GET', 'me'):
            return self.send_json(200, {'me': me})
        if r == ('GET', 'myip'):
            return self.send_json(200, {'ip': self.client_ip(), 'tunnel': self.tunnel, 'onsite': self.onsite()})
        if r == ('PUT', 'me'):
            return self.update_me(me)
        if head == 'meetings':
            return self.meetings(method, parts[1:], q, me)
        if head == 'people':
            return self.people(method, parts[1:], me)
        if head == 'settings':
            return self.settings_api(method, parts[1:], me)
        raise HttpError(404, 'No such API.')

    # ---- sign in ----
    def login(self):
        ip = self.client_ip()
        if SIGNIN_LIMIT.blocked(ip):
            raise HttpError(429, 'Too many wrong PINs. Wait 10 minutes and try again.')
        b = self.body_json()
        with LOCK:
            u = find_user_by_name(clean_name(b.get('name')))
            ok = bool(u and u.get('active', True) and check_pin(u, str(b.get('pin') or '')))
        if not ok:
            SIGNIN_LIMIT.fail(ip)
            raise HttpError(401, 'That name and PIN don\'t match.')
        token = new_session({'uid': u['id']}, SESSION_DAYS * 86400)
        me = resolve_session(token)
        return self.send_json(200, {'me': me}, cookie=self.set_cookie(token, SESSION_DAYS * 86400))

    def guest(self):
        if not setting('guestsAllowed'):
            raise HttpError(403, 'Guests are turned off. Ask for a sign-in.')
        ip = self.client_ip()
        if CODE_LIMIT.blocked(ip):
            raise HttpError(429, 'Too many tries. Wait a few minutes.')
        b = self.body_json()
        # A QR sheet on a factory wall: no name to type. Guests show as Guest + a short tag.
        name = clean_name(b.get('name'), 40) or 'Guest ' + secrets.token_hex(2).upper()
        lang = b.get('lang')
        if lang not in enabled_langs():
            raise HttpError(400, 'Please choose your language.')
        with LOCK:
            m = meeting_by_code(b.get('code'))
        if not m or m['status'] == 'ended':
            CODE_LIMIT.fail(ip)
            raise HttpError(404, 'No meeting with that code is open.')
        if m.get('access') != 'anyone':
            raise HttpError(403, 'This meeting is for signed-in staff. Sign in with your name and PIN.')
        if m.get('onsite') and not self.onsite():
            raise HttpError(403, 'This meeting only takes guests on the company Wi-Fi. Connect to it and scan again.')
        g = {'id': 'g-' + uuid.uuid4().hex[:10], 'name': name, 'lang': lang, 'meeting': m['id']}
        token = new_session({'guest': g}, GUEST_HOURS * 3600)
        return self.send_json(200, {'me': resolve_session(token), 'meeting': m['id']},
                              cookie=self.set_cookie(token, GUEST_HOURS * 3600))

    def update_me(self, me):
        b = self.body_json()
        lang = b.get('lang')
        if lang is not None and lang not in LANG:
            raise HttpError(400, 'Unknown language.')
        with LOCK:
            if me['kind'] == 'user':
                u = STORE.users[me['id']]
                if lang:
                    u['lang'] = lang
                if isinstance(b.get('prefs'), dict):
                    u['prefs'] = {k: v for k, v in b['prefs'].items()
                                  if k in ('voice', 'rate', 'scale', 'mode', 'ptt', 'sink', 'hearSame')}
                STORE.save_users()
            elif lang:
                for s in STORE.sessions.values():
                    if s.get('guest', {}).get('id') == me['id']:
                        s['guest']['lang'] = lang
                STORE.save_sessions()
        return self.send_json(200, {'me': self.me()})

    # ---- meetings ----
    def get_meeting(self, mid):
        m = STORE.meetings.get(mid)
        if not m:
            raise HttpError(404, 'No such meeting.')
        return m

    def meetings(self, method, parts, q, me):
        if not parts:
            if method == 'GET':
                with LOCK:
                    t = now_ms()
                    ms = [m for m in STORE.meetings.values() if visible_in_list(me, m)
                          and (m['status'] != 'ended' or q.get('all') == '1'
                               or t - (m.get('endedAt') or 0) < 3 * 86400000)]
                    ms.sort(key=lambda m: ({'live': 0, 'scheduled': 1, 'ended': 2}[m['status']],
                                           m.get('start') or m.get('createdAt') or 0))
                    return self.send_json(200, {'meetings': [meeting_view(m, me) for m in ms]})
            if method == 'POST':
                me = self.need('host')
                b = self.body_json()
                with LOCK:
                    m = {'id': uuid.uuid4().hex[:10], 'code': new_code(), 'title': '', 'notes': '',
                         'status': 'scheduled', 'start': None, 'duration': 60,
                         'access': 'staff', 'hosts': [], 'listeners': [], 'kicked': [],
                         'createdBy': me['id'], 'createdAt': now_ms()}
                    if b.get('instant'):
                        # Instant meeting: live now, anyone with the QR code listens, no sign-up.
                        if not setting('guestsAllowed'):
                            raise HttpError(400, 'Guests are turned off in settings, so a QR meeting cannot be opened.')
                        b.setdefault('title', 'Meeting ' + time.strftime('%b %d %H:%M'))
                        b['access'] = 'anyone'
                    b.setdefault('title', '')
                    err = clean_meeting_fields(b, m, me)
                    if err:
                        raise HttpError(400, err)
                    if b.get('instant'):
                        m['status'], m['startedAt'] = 'live', now_ms()
                    STORE.meetings[m['id']] = m
                    STORE.save_meetings()
                    if m['status'] == 'live':
                        live_for(m['id'], create=True)
                    return self.send_json(200, {'meeting': meeting_view(m, me)})
            raise HttpError(405, 'Method not allowed.')

        mid, action = parts[0], (parts[1] if len(parts) > 1 else '')
        with LOCK:
            m = self.get_meeting(mid)
        if (method, action) == ('GET', ''):
            if not (can_join(me, m) or visible_in_list(me, m)):
                raise HttpError(403, 'You are not invited to this meeting.')
            with LOCK:
                return self.send_json(200, {'meeting': meeting_view(m, me)})
        if (method, action) == ('GET', 'events'):
            return self.events(m, me, q)
        if (method, action) == ('GET', 'transcript'):
            if not is_host(me, m):
                raise HttpError(403, 'Only the meeting\'s speakers and admins can download the transcript.')
            data = transcript_text(m).encode('utf-8')
            fn = 'ctranslate-%s-%s.txt' % (re.sub(r'[^A-Za-z0-9]+', '-', m['title']).strip('-')[:40] or 'meeting',
                                      time.strftime('%Y%m%d-%H%M', time.localtime((m.get('startedAt') or now_ms()) / 1000)))
            return self.send_bytes(200, data, 'text/plain; charset=utf-8', 'no-store',
                                   {'Content-Disposition': 'attachment; filename="%s"' % fn})
        if (method, action) == ('POST', 'leave'):
            b = self.body_json()
            lv = live_for(m['id'])
            if lv:
                c = lv.clients.get(str(b.get('cid')))
                if c and c.uid == me['id']:
                    lv.remove(c.cid)
            return self.send_json(200, {'ok': True})
        if (method, action) == ('POST', 'prefs'):
            b = self.body_json()
            lv = live_for(m['id'])
            c = lv and lv.clients.get(str(b.get('cid')))
            if c and c.uid == me['id']:
                c.voice = bool(b.get('voice'))
                lv.roster()
            return self.send_json(200, {'ok': True})

        # host-only from here
        if not is_host(me, m):
            raise HttpError(403, 'Only this meeting\'s speakers and admins can do that.')
        if (method, action) == ('PUT', ''):
            b = self.body_json()
            with LOCK:
                err = clean_meeting_fields(b, m, me)
                if err:
                    raise HttpError(400, err)
                STORE.save_meetings()
                return self.send_json(200, {'meeting': meeting_view(m, me)})
        if (method, action) == ('DELETE', ''):
            end_live(m['id'], 'ended')
            with LOCK:
                STORE.meetings.pop(m['id'], None)
                STORE.save_meetings()
            try:
                os.remove(path(os.path.join('transcripts', m['id'] + '.jsonl')))
            except OSError:
                pass
            return self.send_json(200, {'ok': True})
        if (method, action) == ('POST', 'start'):
            with LOCK:
                if m['status'] != 'live':
                    m['status'], m['startedAt'], m['endedAt'] = 'live', now_ms(), None
                    STORE.save_meetings()
            lv = live_for(m['id'], create=True)
            lv.broadcast('status', {'status': 'live'})
            WAITING.notify(m['id'], 'live')
            with LOCK:
                return self.send_json(200, {'meeting': meeting_view(m, me)})
        if (method, action) == ('POST', 'end'):
            # A standing room (a QR sign on the wall) goes back to waiting for its next session;
            # its listeners stay on and hear the next one start. Anything else ends for good.
            st = 'scheduled' if m.get('standing') else 'ended'
            with LOCK:
                m['status'], m['endedAt'] = st, now_ms()
                STORE.save_meetings()
            end_live(m['id'], st)
            WAITING.notify(m['id'], st)
            with LOCK:
                return self.send_json(200, {'meeting': meeting_view(m, me)})
        if (method, action) == ('POST', 'kick'):
            b = self.body_json()
            lv = live_for(m['id'])
            c = lv and lv.clients.get(str(b.get('cid')))
            if not c:
                raise HttpError(404, 'That person has already left.')
            with LOCK:
                if not c.guest and c.uid not in m.setdefault('kicked', []):
                    m['kicked'].append(c.uid)
                    STORE.save_meetings()
                if c.guest:
                    for k, s in list(STORE.sessions.items()):
                        if s.get('guest', {}).get('id') == c.uid:
                            del STORE.sessions[k]
                    STORE.save_sessions()
            c.send('kicked', {})
            c.close()
            lv.remove(c.cid)
            return self.send_json(200, {'ok': True})
        if (method, action) == ('POST', 'newcode'):
            # A sheet went somewhere it should not: every printed copy stops working.
            with LOCK:
                m['code'] = new_code()
                STORE.save_meetings()
                for k, s in list(STORE.sessions.items()):
                    if s.get('guest', {}).get('meeting') == m['id']:
                        del STORE.sessions[k]
                STORE.save_sessions()
            lv = live_for(m['id'])
            if lv:
                for c in lv.all():
                    if c.guest:
                        c.send('kicked', {'reason': 'code'})
                        lv.remove(c.cid)
            with LOCK:
                return self.send_json(200, {'meeting': meeting_view(m, me)})
        if (method, action) == ('POST', 'unkick'):
            b = self.body_json()
            with LOCK:
                m['kicked'] = [i for i in m.get('kicked', []) if i != b.get('uid')]
                STORE.save_meetings()
            return self.send_json(200, {'ok': True})
        if (method, action) in (('POST', 'speech'), ('POST', 'audio')):
            if m['status'] != 'live':
                raise HttpError(409, 'Start the meeting first.')
            lv = live_for(m['id'], create=True)
            if action == 'audio':
                return self.audio_phrase(lv, me, q)
            b = self.body_json()
            lang = b.get('lang') if b.get('lang') in LANG else me['lang']
            text = re.sub(r'\s+', ' ', str(b.get('text') or '')).strip()[:2000]
            try:
                n = max(0, int(b.get('n') or 0))
            except (TypeError, ValueError):
                n = 0
            if b.get('final'):
                if n:
                    lv.final_n[me['id']] = max(n, lv.final_n.get(me['id'], 0))
                if text:
                    lv.submit(me, lang, text)
                else:
                    lv.clear_interim(me)
            elif text:
                lv.interim(me, lang, text, n)
            return self.send_json(200, {'ok': True})
        raise HttpError(404, 'No such API.')

    def audio_phrase(self, lv, me, q):
        if not (setting('serverStt') and google_key()):
            raise HttpError(400, 'Server speech recognition is off. Use the device recognizer.')
        n = int(self.headers.get('Content-Length') or 0)
        if n <= 0 or n > MAX_AUDIO_BODY:
            raise HttpError(413, 'Audio phrase too large.')
        pcm = self.rfile.read(n)
        lang = q.get('lang') if q.get('lang') in LANG else me['lang']
        try:
            text = stt_google(pcm, lang)
        except Exception as e:
            log('!! stt', repr(e))
            raise HttpError(502, 'Speech recognition failed: %s' % e)
        if text:
            lv.submit(me, lang, text)
        return self.send_json(200, {'text': text})

    def events(self, m, me, q):
        """The live stream for one client. Also serves a scheduled meeting's waiting room."""
        if not can_join(me, m):
            raise HttpError(403, 'You are not invited to this meeting.')
        if me['kind'] == 'guest' and m.get('onsite') and not self.onsite():
            raise HttpError(403, 'This meeting only takes guests on the company Wi-Fi.')
        if m['status'] == 'ended':
            raise HttpError(410, 'This meeting has ended.')
        lang = q.get('lang') if q.get('lang') in LANG else me['lang']
        self.send_response(200)
        self.send_header('Content-Type', 'text/event-stream; charset=utf-8')
        self.send_header('Cache-Control', 'no-cache, no-transform')
        self.send_header('X-Accel-Buffering', 'no')
        self.send_header('Connection', 'close')
        self.headers_common()
        self.end_headers()
        self.close_connection = True
        w = self.wfile

        def write(event, data, eid=None):
            s = ''
            if eid is not None:
                s += 'id: %s\n' % eid
            s += 'event: %s\ndata: %s\n\n' % (event, json.dumps(data, ensure_ascii=False))
            w.write(s.encode('utf-8'))
            w.flush()

        w.write(b'retry: 3000\n\n')
        # Waiting room: a scheduled meeting streams nothing but its status change.
        if m['status'] == 'scheduled':
            ev = WAITING.wait_handle(m['id'])
            write('status', {'status': 'scheduled'})
            try:
                while True:
                    got = ev.wait(15)
                    if got is not None:
                        write('status', {'status': got})
                        return
                    w.write(b': ping\n\n')
                    w.flush()
            except (BrokenPipeError, ConnectionResetError, OSError, ssl.SSLError):
                return
            finally:
                WAITING.release(m['id'], ev)
        lv = live_for(m['id'], create=True)
        c = Client(me, lang, q.get('voice') == '1')
        since = 0
        try:
            since = int(self.headers.get('Last-Event-ID') or q.get('since') or 0)
        except ValueError:
            pass
        try:
            write('hello', {'cid': c.cid, 'lang': lang, 'status': 'live', 'resumed': since > 0,
                            'history': lv.history_for(lang, since, 30 if since else 12)})
            lv.add(c)
            while True:
                try:
                    item = c.q.get(timeout=15)
                except queue.Empty:
                    w.write(b': ping\n\n')
                    w.flush()
                    continue
                if item is None:
                    break
                write(*item)
        except (BrokenPipeError, ConnectionResetError, OSError, ssl.SSLError):
            pass
        finally:
            lv.remove(c.cid)

    # ---- people ----
    def people(self, method, parts, me):
        if method == 'GET' and not parts:
            me = self.need('host')
            with LOCK:
                full = me['role'] == 'admin'
                us = sorted(STORE.users.values(), key=lambda u: u['name'].casefold())
                return self.send_json(200, {'people': [public_user(u, full) for u in us
                                                       if full or u.get('active', True)]})
        me = self.need('admin')
        b = self.body_json() if method in ('POST', 'PUT') else {}
        with LOCK:
            if method == 'POST' and not parts:
                name = clean_name(b.get('name'))
                if not name:
                    raise HttpError(400, 'A person needs a name.')
                if find_user_by_name(name):
                    raise HttpError(409, 'Someone is already called that. Add a last initial.')
                u = {'id': uuid.uuid4().hex[:10], 'name': name, 'active': True, 'created': now_ms()}
                err = self.apply_person(u, b, new=True)
                if err:
                    raise HttpError(400, err)
                STORE.users[u['id']] = u
                STORE.save_users()
                return self.send_json(200, {'person': public_user(u, True)})
            if not parts or parts[0] not in STORE.users:
                raise HttpError(404, 'No such person.')
            u = STORE.users[parts[0]]
            if method == 'PUT':
                if 'name' in b:
                    name = clean_name(b['name'])
                    other = find_user_by_name(name)
                    if not name or (other and other['id'] != u['id']):
                        raise HttpError(409, 'That name is empty or taken.')
                    u['name'] = name
                err = self.apply_person(u, b, new=False)
                if err:
                    raise HttpError(400, err)
                if u['role'] != 'admin' or not u.get('active', True):
                    if not any(x['role'] == 'admin' and x.get('active', True) for x in STORE.users.values()):
                        raise HttpError(400, 'Keep at least one active admin.')
                STORE.save_users()
                if not u.get('active', True) or b.get('pin'):
                    self.drop_user_sessions(u['id'], keep=None if u['id'] != me['id'] else self.token())
                return self.send_json(200, {'person': public_user(u, True)})
            if method == 'DELETE':
                if u['id'] == me['id']:
                    raise HttpError(400, 'You cannot remove yourself.')
                if u['role'] == 'admin' and sum(1 for x in STORE.users.values() if x['role'] == 'admin') < 2:
                    raise HttpError(400, 'Keep at least one admin.')
                del STORE.users[u['id']]
                STORE.save_users()
                self.drop_user_sessions(u['id'])
                for m in STORE.meetings.values():
                    for k in ('hosts', 'listeners'):
                        if u['id'] in m.get(k, []):
                            m[k].remove(u['id'])
                STORE.save_meetings()
                return self.send_json(200, {'ok': True})
        raise HttpError(405, 'Method not allowed.')

    def apply_person(self, u, b, new):
        if 'role' in b or new:
            if b.get('role') not in ROLES:
                return 'Role must be admin, speaker or listener.'
            u['role'] = b['role']
        if 'lang' in b or new:
            if b.get('lang') not in LANG:
                return 'Choose a language.'
            u['lang'] = b['lang']
        if 'active' in b:
            u['active'] = bool(b['active'])
        pin = str(b.get('pin') or '')
        if new and not pin:
            return 'Set a PIN.'
        if pin:
            if not re.fullmatch(r'\d{4,12}', pin):
                return 'A PIN is 4 to 12 digits.'
            if u.get('role') == 'admin' and len(pin) < 6:
                return 'An admin PIN needs at least 6 digits.'
            set_pin(u, pin)
        return None

    def drop_user_sessions(self, uid, keep=None):
        keep_h = hashlib.sha256(keep.encode()).hexdigest() if keep else None
        for k, s in list(STORE.sessions.items()):
            if s.get('uid') == uid and k != keep_h:
                del STORE.sessions[k]
        STORE.save_sessions()

    # ---- settings ----
    def settings_api(self, method, parts, me):
        self.need('admin')
        if method == 'GET' and not parts:
            with LOCK:
                s = {k: v for k, v in STORE.settings.items() if k not in SECRET_KEYS}
                for k in SECRET_KEYS:
                    own = STORE.settings.get(k) or ''
                    env = os.environ.get(ENV_KEYS[k], '')
                    s[k + 'Set'] = bool(own or env)
                    s[k + 'Hint'] = ('…' + own[-4:]) if own else ('from the environment' if env else '')
                s['claudeModels'] = CLAUDE_MODELS
                return self.send_json(200, {'settings': s})
        if method == 'PUT' and not parts:
            b = self.body_json()
            with LOCK:
                err = apply_settings(b)
                if err:
                    raise HttpError(400, err)
                STORE.save_settings()
            return self.settings_api('GET', [], me)
        if method == 'POST' and parts == ['test']:
            return self.send_json(200, {'results': engine_test()})
        if method == 'POST' and parts == ['voice-sample']:
            # Admin -> Settings -> Hear each language: one English sentence, translated and voiced
            # exactly as a meeting would do it.
            b = self.body_json()
            text = re.sub(r'\s+', ' ', str(b.get('text') or '')).strip()[:300]
            lang = b.get('lang')
            if not text or lang not in LANG:
                raise HttpError(400, 'Give a sentence and a language.')
            said = text if lang == 'en' else translate_many(text, 'en', [lang], []).get(lang)
            if not said:
                raise HttpError(502, 'Translation failed. Check the keys with TEST ENGINES.')
            mp3 = synthesize(said, lang) if setting('ttsEngine') == 'google' else None
            return self.send_json(200, {'text': said, 'url': audio_put(mp3) if mp3 else None,
                                        'voice': voice_source(lang) if mp3 else 'The phone\'s own voice'})
        raise HttpError(404, 'No such API.')


def apply_settings(b):
    s = STORE.settings
    for k in ('orgName', 'glossary'):
        if k in b:
            s[k] = str(b[k] or '')[:200 if k == 'orgName' else 8000]
    if 'publicUrl' in b:
        u = str(b['publicUrl'] or '').strip().rstrip('/')
        if u and not re.fullmatch(r'https://[A-Za-z0-9.\-]+(:\d+)?', u):
            return 'The public address looks like https://meet.example.com'
        s['publicUrl'] = u
    if 'languages' in b:
        ls = [c for c in b['languages'] if c in LANG] if isinstance(b['languages'], list) else []
        if not ls:
            return 'Keep at least one language.'
        s['languages'] = ls
    choices = {'mtEngine': ('free', 'google', 'claude'), 'ttsEngine': ('google', 'device', 'off'),
               'claudeModel': tuple(CLAUDE_MODELS), 'claudeEffort': ('low', 'medium', 'high'),
               'voiceGender': ('FEMALE', 'MALE', 'NEUTRAL'), 'voiceTier': VOICE_TIERS}
    for k, opts in choices.items():
        if k in b:
            if b[k] not in opts:
                return '%s must be one of %s' % (k, ', '.join(opts))
            s[k] = b[k]
    if 'siteIps' in b:
        ips = b['siteIps'] if isinstance(b['siteIps'], list) else str(b['siteIps'] or '').replace(',', ' ').split()
        clean = []
        for x in ips:
            try:
                clean.append(str(ipaddress.ip_network(str(x).strip(), strict=False)))
            except ValueError:
                return '"%s" is not an internet address (like 203.0.113.7 or 203.0.113.0/28).' % x
        s['siteIps'] = clean
    if 'azureRegion' in b:
        r = str(b['azureRegion'] or '').strip().lower()
        if r and not re.fullmatch(r'[a-z0-9]{3,30}', r):
            return 'Azure region looks like eastus or westeurope.'
        s['azureRegion'] = r or 'eastus'
    for k in ('serverStt', 'liveCaptions', 'guestsAllowed', 'freeVoice'):
        if k in b:
            s[k] = bool(b[k])
    if 'speakingRate' in b:
        try:
            s['speakingRate'] = max(0.5, min(2.0, float(b['speakingRate'])))
        except (TypeError, ValueError):
            return 'Speaking rate is a number from 0.5 to 2.'
    if 'transcriptDays' in b:
        try:
            s['transcriptDays'] = max(1, min(3650, int(b['transcriptDays'])))
        except (TypeError, ValueError):
            return 'Transcript days is a number.'
    if 'voices' in b and isinstance(b['voices'], dict):
        s['voices'] = {k: str(v)[:80] for k, v in b['voices'].items() if k in LANG and v}
    with VOICES_LOCK:
        VOICES['picked'] = {}         # voice choices follow the new settings
    for k in SECRET_KEYS:
        if k in b and b[k] is not None:
            v = str(b[k]).strip()
            if v:
                s[k] = v
            else:
                s.pop(k, None)
    return None


def voice_source(lang):
    """Which source would voice this language right now (for the admin's test)."""
    if google_key() and voice_for(lang):
        return 'Google: %s' % (voice_for(lang).get('name') or 'default voice')
    if azure_key() and lang in AZURE_VOICES:
        return 'Azure: %s' % AZURE_VOICES[lang][2 if setting('voiceGender') == 'MALE' else 1]
    if setting('freeVoice'):
        return 'Free Google Translate read-aloud'
    return 'The listener\'s own device voice'


def engine_test():
    """Admin's Test button: one short phrase through every engine that is configured."""
    out = []
    phrase = 'Good morning, everyone. Safety first today.'

    def attempt(name, fn):
        t = time.time()
        try:
            r = fn()
            out.append({'engine': name, 'ok': True, 'ms': int((time.time() - t) * 1000), 'result': r})
        except Exception as e:
            out.append({'engine': name, 'ok': False, 'ms': int((time.time() - t) * 1000), 'result': str(e)[:300]})
    attempt('Free translation (Google public endpoint)', lambda: translate_free(phrase, 'en', 'es'))
    if google_key():
        attempt('Google Cloud Translation', lambda: translate_google(phrase, 'en', 'es'))
        attempt('Google Text-to-Speech', lambda: '%d bytes of MP3, voice %s' % (
            len(tts_google('Buenos días a todos.', 'es') or b''), voice_for('es').get('name', 'default')))
        attempt('Google Speech-to-Text', lambda: 'reachable (heard: "%s")' % stt_google(b'\0' * 32000, 'en'))
    else:
        out.append({'engine': 'Google Cloud (Translation, Text-to-Speech, Speech-to-Text)', 'ok': False,
                    'result': 'No Google API key set.'})
    if azure_key():
        attempt('Azure voice (Swahili, backup)', lambda: '%d bytes of MP3' % len(tts_azure('Habari za asubuhi.', 'sw') or b''))
    if setting('freeVoice'):
        attempt('Free read-aloud (Swahili)', lambda: '%d bytes of MP3' % len(tts_free('Habari za asubuhi.', 'sw') or b''))
    for l in enabled_langs():
        if l != 'en':
            attempt('Voice for %s' % LANG[l]['label'], lambda l=l: voice_source(l))
    if anthropic_key():
        attempt('Claude (%s)' % setting('claudeModel'),
                lambda: translate_claude(phrase, 'en', ['es', 'fr'], []))
    else:
        out.append({'engine': 'Claude', 'ok': False, 'result': 'No Anthropic API key set.'})
    return out


class Waiting:
    """Clients waiting for a scheduled meeting to start (or be cancelled)."""

    class Handle:
        def __init__(self):
            self.ev = threading.Event()
            self.value = None

        def wait(self, t):
            return self.value if self.ev.wait(t) else None

    def __init__(self):
        self.lock = threading.Lock()
        self.waiters = collections.defaultdict(set)

    def wait_handle(self, mid):
        h = Waiting.Handle()
        with self.lock:
            self.waiters[mid].add(h)
        return h

    def release(self, mid, h):
        with self.lock:
            self.waiters[mid].discard(h)

    def notify(self, mid, status):
        with self.lock:
            hs = list(self.waiters.pop(mid, ()))
        for h in hs:
            h.value = status
            h.ev.set()


WAITING = Waiting()
TUNNEL_IN_USE = [False]     # set when the server runs with --tunnel-port


# ---------------------------------------------------------------------------
# Servers
# ---------------------------------------------------------------------------
class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True
    request_queue_size = 128


class TLSServer(Server):
    """HTTPS with the certificate reloaded when its file changes (Let's Encrypt renewals)."""

    def __init__(self, addr, handler, cert, key):
        self.cert, self.key = cert, key
        self.ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        self.ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        self.ctx.load_cert_chain(cert, key)
        self.stamp = self._stamp()
        super().__init__(addr, handler)
        threading.Thread(target=self._watch, daemon=True).start()

    def _stamp(self):
        try:
            return (os.path.getmtime(self.cert), os.path.getmtime(self.key))
        except OSError:
            return None

    def _watch(self):
        while True:
            time.sleep(60)
            s = self._stamp()
            if s and s != self.stamp:
                try:
                    self.ctx.load_cert_chain(self.cert, self.key)
                    self.stamp = s
                    log('== certificate reloaded')
                except (ssl.SSLError, OSError) as e:
                    log('!! certificate reload failed, keeping the old one:', e)

    def get_request(self):
        # The handshake runs in the request's own thread (finish_request), so one slow
        # or broken client can never hold up the accept loop.
        sock, addr = self.socket.accept()
        return self.ctx.wrap_socket(sock, server_side=True, do_handshake_on_connect=False), addr

    def finish_request(self, request, client_address):
        request.settimeout(30)
        try:
            request.do_handshake()
        except (ssl.SSLError, OSError):
            return
        # Per operation, not per connection: the event stream writes at least every 15 s.
        request.settimeout(120)
        super().finish_request(request, client_address)

    def handle_error(self, request, client_address):
        pass                        # handshakes from scanners and stale phones: not worth a traceback


def main():
    global DATA, STORE
    ap = argparse.ArgumentParser(description='CTRANSLATE live translated meetings')
    ap.add_argument('--port', type=int, default=8453)
    ap.add_argument('--bind', default='0.0.0.0')
    ap.add_argument('--cert')
    ap.add_argument('--key')
    ap.add_argument('--http', action='store_true', help='plain http (local testing only)')
    ap.add_argument('--tunnel-port', type=int, default=0, help='plain http on 127.0.0.1 for cloudflared')
    ap.add_argument('--data', default=ROOT)
    a = ap.parse_args()
    DATA = os.path.abspath(a.data)
    os.makedirs(DATA, exist_ok=True)
    STORE = Store()
    ensure_admin()
    # Meetings that were live when the server stopped carry on.
    for m in STORE.meetings.values():
        if m['status'] == 'live':
            live_for(m['id'], create=True)
    threading.Thread(target=housekeeping, daemon=True).start()

    servers = []
    if a.http:
        servers.append(Server((a.bind, a.port), Handler))
        log('== CTRANSLATE %s on http://%s:%d (testing mode)' % (BUILD_VERSION, a.bind, a.port))
    else:
        if not (a.cert and a.key):
            sys.exit('--cert and --key are required (or --http for local testing)')
        servers.append(TLSServer((a.bind, a.port), Handler, a.cert, a.key))
        log('== CTRANSLATE %s on https://%s:%d' % (BUILD_VERSION, a.bind, a.port))
    if a.tunnel_port:
        t = Server(('127.0.0.1', a.tunnel_port), Handler)
        t.tunnel = True
        TUNNEL_IN_USE[0] = True
        servers.append(t)
        log('== tunnel listener on http://127.0.0.1:%d' % a.tunnel_port)
    for s in servers[1:]:
        threading.Thread(target=s.serve_forever, daemon=True).start()
    try:
        servers[0].serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == '__main__':
    main()
