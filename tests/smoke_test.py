#!/usr/bin/env python3
"""End-to-end check of the server with fake engines (no network, no keys):

    python3 tests/smoke_test.py

Starts server.py on a spare port with CTRANSLATE_FAKE_ENGINES=1 and a temporary data
directory, then walks the whole flow: admin sign-in, adding a speaker, an
instant QR meeting (standing room), a guest joining by code with no name, the
live stream (phrase, translation, voice clip, live captions), ordering of
posts, ending a session (standing room returns to waiting), a new code
cutting off guests, the transcript, and the guards (CSRF header, roles,
wrong PIN). Exits non-zero on the first failure."""
import http.client, json, os, re, shutil, socket, subprocess, sys, tempfile, threading, time

HERE = os.path.dirname(os.path.abspath(__file__))
SERVER = os.path.join(HERE, '..', 'server.py')


def free_port():
    s = socket.socket()
    s.bind(('127.0.0.1', 0))
    p = s.getsockname()[1]
    s.close()
    return p


PORT = free_port()
DATA = tempfile.mkdtemp(prefix='ctranslate-test-')
PROC = None
FAILS = 0


def check(cond, what):
    global FAILS
    print(('ok   ' if cond else 'FAIL ') + what)
    if not cond:
        FAILS += 1
        raise SystemExit(1)


class Client:
    def __init__(self):
        self.cookie = None

    def req(self, method, path, body=None, headers=None, raw=False):
        c = http.client.HTTPConnection('127.0.0.1', PORT, timeout=10)
        h = {'X-VM': '1'}
        if self.cookie:
            h['Cookie'] = 'vm=' + self.cookie
        data = None
        if isinstance(body, (bytes, bytearray)):
            data = bytes(body)
            h['Content-Type'] = 'application/octet-stream'
        elif body is not None:
            data = json.dumps(body).encode()
            h['Content-Type'] = 'application/json'
        h.update(headers or {})
        c.request(method, path, data, h)
        r = c.getresponse()
        for k, v in r.getheaders():
            if k.lower() == 'set-cookie':
                m = re.match(r'vm=([^;]*)', v)
                if m:
                    self.cookie = m.group(1) or None
        payload = r.read()
        c.close()
        if raw:
            return r.status, payload
        try:
            return r.status, json.loads(payload or b'{}')
        except ValueError:
            return r.status, {'raw': payload[:200]}


class Events:
    """Reads one client's event stream in a thread."""

    def __init__(self, client, mid, lang, since=0):
        self.events = []
        self.lock = threading.Lock()
        self.conn = http.client.HTTPConnection('127.0.0.1', PORT, timeout=30)
        q = '/api/meetings/%s/events?lang=%s&voice=1' % (mid, lang) + ('&since=%d' % since if since else '')
        self.conn.request('GET', q, headers={'Cookie': 'vm=' + client.cookie})
        self.resp = self.conn.getresponse()
        self.status = self.resp.status
        threading.Thread(target=self.read, daemon=True).start()

    def read(self):
        ev, data = None, []
        try:
            while True:
                line = self.resp.fp.readline()
                if not line:
                    break
                line = line.decode('utf-8').rstrip('\n')
                if line.startswith('event:'):
                    ev = line[6:].strip()
                elif line.startswith('data:'):
                    data.append(line[5:].strip())
                elif line == '':
                    if ev:
                        with self.lock:
                            self.events.append((ev, json.loads('\n'.join(data) or '{}')))
                    ev, data = None, []
        except Exception:
            pass
        with self.lock:
            self.events.append(('<closed>', {}))

    def wait(self, pred, timeout=6):
        end = time.time() + timeout
        while time.time() < end:
            with self.lock:
                for e in self.events:
                    if pred(e):
                        return e
            time.sleep(0.05)
        return None

    def close(self):
        try:
            self.conn.sock.shutdown(socket.SHUT_RDWR)
        except Exception:
            pass
        self.conn.close()


def main():
    global PROC
    env = dict(os.environ, CTRANSLATE_FAKE_ENGINES='1')
    PROC = subprocess.Popen([sys.executable, SERVER, '--http', '--bind', '127.0.0.1', '--port', str(PORT), '--data', DATA],
                            env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    for _ in range(100):
        try:
            socket.create_connection(('127.0.0.1', PORT), timeout=0.2).close()
            break
        except OSError:
            time.sleep(0.05)
    pin = open(os.path.join(DATA, 'admin-pin.txt')).read().strip()

    anon = Client()
    st, cfg = anon.req('GET', '/api/config')
    check(st == 200 and [l['id'] for l in cfg['languages']] == ['en', 'es', 'fr', 'sw'], 'config: English, Spanish, French, Swahili offered')
    for page in ('/', '/listen', '/speak', '/admin', '/print', '/sw.js', '/app.js', '/i18n.js', '/qr.js', '/apps/listen.webmanifest'):
        st, body = anon.req('GET', page, raw=True)
        check(st == 200 and b'__BUILD__' not in body, 'serves %s with the build stamped in' % page)
    st, _ = anon.req('GET', '/../server.py', raw=True)
    check(st == 404, 'no path traversal out of public/')

    st, r = anon.req('POST', '/api/login', {'name': 'Admin', 'pin': pin}, headers={'X-VM': ''})
    check(st == 403, 'writes without the X-VM header are refused')
    st, r = anon.req('POST', '/api/login', {'name': 'Admin', 'pin': '00000000'})
    check(st == 401, 'wrong PIN refused')

    admin = Client()
    st, r = admin.req('POST', '/api/login', {'name': 'admin', 'pin': pin})
    check(st == 200 and r['me']['role'] == 'admin', 'admin signs in (name is case-insensitive)')
    st, r = admin.req('PUT', '/api/settings', {'ttsEngine': 'google', 'freeVoice': True, 'mtEngine': 'claude', 'anthropicKey': 'test-key'})
    check(st == 200 and r['settings']['claudeModel'] == 'claude-haiku-5-5', 'settings: Claude Haiku 5.5 is the default model')
    admin.req('PUT', '/api/settings', {'googleKey': 'test-key'})
    st, r = admin.req('POST', '/api/settings/voice-sample', {'text': 'Good morning, welcome to the training, my name is Wes.', 'lang': 'sw'})
    check(st == 200 and r['text'].startswith('[claude sw]') and r['url'].startswith('/api/audio/'), 'admin hears a language: translated and voiced')
    st, clip = admin.req('GET', r['url'], raw=True)
    check(st == 200 and clip.startswith(b'ID3'), 'the sample plays as MP3')
    admin.req('PUT', '/api/settings', {'googleKey': ''})
    st, r = admin.req('POST', '/api/people', {'name': 'Maria Lopez', 'role': 'speaker', 'lang': 'en', 'pin': '482913'})
    check(st == 200, 'admin adds a speaker')
    st, r = admin.req('POST', '/api/people', {'name': 'maria lopez', 'role': 'listener', 'lang': 'es', 'pin': '1234'})
    check(st == 409, 'duplicate names refused')

    sp = Client()
    st, r = sp.req('POST', '/api/login', {'name': 'Maria Lopez', 'pin': '482913'})
    check(st == 200 and r['me']['role'] == 'speaker', 'speaker signs in')
    st, r = sp.req('GET', '/api/settings')
    check(st == 403, 'a speaker cannot read settings or keys')

    st, r = sp.req('POST', '/api/meetings', {'instant': True, 'title': 'Line 3 briefing', 'standing': True, 'onsite': False})
    m = r.get('meeting', {})
    check(st == 200 and m['status'] == 'live' and m['access'] == 'anyone' and m['standing'], 'instant QR meeting is live, open to guests, standing')
    code, mid = m['code'], m['id']

    st, info = anon.req('GET', '/api/code/' + code.lower())
    check(st == 200 and info['guests'] and info['status'] == 'live', 'code lookup works, any case')
    st, _ = anon.req('GET', '/j/' + code, raw=True)
    check(st == 302, 'QR link /j/<code> redirects to the listener app')

    g1, g2 = Client(), Client()
    st, r = g1.req('POST', '/api/guest', {'code': code, 'lang': 'es'})
    check(st == 200 and r['me']['name'].startswith('Guest '), 'guest joins with no name (Spanish)')
    st, r = g2.req('POST', '/api/guest', {'code': code, 'lang': 'sw'})
    check(st == 200, 'second guest joins (Swahili)')
    st, r = g1.req('POST', '/api/meetings', {'instant': True})
    check(st == 401 or st == 403, 'a guest cannot create meetings')

    es = Events(g1, mid, 'es')
    sw = Events(g2, mid, 'sw')
    spk = Events(sp, mid, 'en')
    check(es.wait(lambda e: e[0] == 'hello') is not None, 'guest stream says hello')
    check(spk.wait(lambda e: e[0] == 'roster' and len(e[1]['people']) == 3) is not None, 'speaker sees 3 connected')

    st, _ = sp.req('POST', '/api/meetings/%s/speech' % mid, {'text': 'good morning everyone', 'final': False, 'lang': 'en', 'n': 1})
    check(st == 200, 'speaker posts live words')
    check(es.wait(lambda e: e[0] == 'live' and e[1]['text'] == '[es] good morning everyone') is not None, 'live captions reach the Spanish guest, translated')

    st, _ = sp.req('POST', '/api/meetings/%s/speech' % mid, {'text': 'Good morning, everyone. Safety first.', 'final': True, 'lang': 'en', 'n': 2})
    seg = es.wait(lambda e: e[0] == 'seg')
    check(seg and seg[1]['text'].startswith('[claude es]') and seg[1]['audio'] == 'pending', 'Spanish guest gets the phrase from Claude, voice pending')
    seg_sw = sw.wait(lambda e: e[0] == 'seg')
    check(seg_sw and seg_sw[1]['text'].startswith('[claude sw]'), 'Swahili guest gets it in Swahili')
    mine = spk.wait(lambda e: e[0] == 'seg')
    check(mine and mine[1]['same'] and set(mine[1]['tr']) == {'es', 'sw'}, 'speaker sees own phrase and which languages it went to')
    au = es.wait(lambda e: e[0] == 'audio' and e[1]['id'] == seg[1]['id'])
    check(au and au[1]['url'] and au[1]['url'].startswith('/api/audio/'), 'voice clip arrives for the Spanish guest')
    st, clip = g1.req('GET', au[1]['url'], raw=True)
    check(st == 200 and clip.startswith(b'ID3'), 'voice clip downloads as MP3')

    # A stale live line that lands after its phrase was finished is dropped.
    st, _ = sp.req('POST', '/api/meetings/%s/speech' % mid, {'text': 'stale words', 'final': False, 'lang': 'en', 'n': 1})
    time.sleep(0.4)
    check(es.wait(lambda e: e[0] == 'live' and 'stale' in e[1]['text'], 0.5) is None, 'out-of-order live words are dropped')

    st, _ = sp.req('POST', '/api/meetings/%s/audio?lang=en' % mid, b'\0' * 32000)
    check(st == 400, 'server recognition refused while it is turned off')
    admin.req('PUT', '/api/settings', {'serverStt': True, 'googleKey': 'test-key'})
    st, r = sp.req('POST', '/api/meetings/%s/audio?lang=en' % mid, b'\0' * 32000)
    check(st == 200 and r['text'].startswith('spoken phrase'), 'server recognition turns audio into a phrase')
    check(es.wait(lambda e: e[0] == 'seg' and 'spoken phrase' in (e[1]['text'] or '')) is not None, 'and the guest receives it')

    # Reconnect: a guest who drops and comes back gets what they missed.
    es.close()
    sp.req('POST', '/api/meetings/%s/speech' % mid, {'text': 'You missed this.', 'final': True, 'lang': 'en', 'n': 5})
    time.sleep(0.5)
    es2 = Events(g1, mid, 'es', since=seg[1]['id'])
    hello = es2.wait(lambda e: e[0] == 'hello')
    check(hello and any('You missed this' in h['src'] for h in hello[1]['history']), 'reconnecting guest gets missed phrases')

    # End the session: a standing room goes back to waiting; guests stay attached.
    st, r = sp.req('POST', '/api/meetings/%s/end' % mid)
    check(st == 200 and r['meeting']['status'] == 'scheduled', 'ending a standing room puts it back to waiting')
    check(es2.wait(lambda e: e[0] == 'status' and e[1]['status'] == 'scheduled') is not None, 'guests are told it is waiting')
    wait = Events(g1, mid, 'es')
    check(wait.wait(lambda e: e[0] == 'status' and e[1]['status'] == 'scheduled') is not None, 'guest can wait in the room')
    st, r = sp.req('POST', '/api/meetings/%s/start' % mid)
    check(st == 200 and r['meeting']['status'] == 'live', 'next session starts')
    check(wait.wait(lambda e: e[0] == 'status' and e[1]['status'] == 'live') is not None, 'waiting guest is told it is live')

    st, txt = sp.req('GET', '/api/meetings/%s/transcript' % mid, raw=True)
    check(st == 200 and b'Good morning, everyone' in txt and b'SW: [claude sw]' in txt, 'transcript has the phrase in every language')
    st, _ = g1.req('GET', '/api/meetings/%s/transcript' % mid, raw=True)
    check(st == 403, 'guests cannot download the transcript')

    # New code: every printed sheet and guest session stops working.
    st, r = sp.req('POST', '/api/meetings/%s/newcode' % mid)
    check(st == 200 and r['meeting']['code'] != code, 'new code issued')
    st, _ = anon.req('GET', '/api/code/' + code)
    check(st == 404, 'the old code no longer works')
    st, _ = g1.req('GET', '/api/me')
    check(st == 401, 'guests on the old code are signed out')

    # Ordinary meeting, invited only; listeners outside it cannot join.
    st, r = admin.req('POST', '/api/people', {'name': 'Jean', 'role': 'listener', 'lang': 'fr', 'pin': '5555'})
    jid = r['person']['id']
    st, r = sp.req('POST', '/api/meetings', {'title': 'Managers', 'access': 'invited', 'start': int(time.time() * 1000) + 3600000})
    m2 = r['meeting']
    check(st == 200 and m2['status'] == 'scheduled' and m2['hosts'][0]['name'] == 'Maria Lopez', 'scheduled meeting; creator is a speaker')
    jean = Client()
    jean.req('POST', '/api/login', {'name': 'Jean', 'pin': '5555'})
    st, _ = jean.req('GET', '/api/meetings/' + m2['id'])
    check(st == 403, 'uninvited listener cannot open an invited-only meeting')
    sp.req('PUT', '/api/meetings/' + m2['id'], {'listeners': [jid]})
    st, r = jean.req('GET', '/api/meetings')
    check(any(x['id'] == m2['id'] for x in r['meetings']), 'after the invite it shows in their list')
    st, r = sp.req('POST', '/api/meetings/%s/speech' % m2['id'], {'text': 'x', 'final': True})
    check(st == 409, 'cannot speak before the meeting starts')
    st, r = sp.req('POST', '/api/meetings/%s/end' % m2['id'])
    check(r['meeting']['status'] == 'ended', 'a normal meeting ends for good')
    st, _ = anon.req('GET', '/api/code/' + m2['code'])
    check(st == 404, 'an ended meeting\'s code is closed')

    for e in (es2, sw, spk, wait):
        e.close()
    print('\nall checks passed')


if __name__ == '__main__':
    try:
        main()
    finally:
        if PROC:
            PROC.terminate()
            try:
                out = PROC.communicate(timeout=5)[0]
            except Exception:
                out = ''
            if FAILS and out:
                print('\n--- server log ---\n' + out[-3000:])
        shutil.rmtree(DATA, ignore_errors=True)
