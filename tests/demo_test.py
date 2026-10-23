#!/usr/bin/env python3
"""The --demo test accounts:

    python3 tests/demo_test.py

Starts server.py with --demo (fake engines, temporary data), signs in as each invented
account with its PIN, checks roles, languages and who sees which demo meeting, that a
guest joins the demo room by its code, that demo-accounts.txt matches, and that a second
start with --demo adds no meetings and puts a changed PIN back. Exits non-zero on the
first failure."""
import http.client, json, os, shutil, socket, subprocess, sys, tempfile, time

HERE = os.path.dirname(os.path.abspath(__file__))
SERVER = os.path.join(HERE, '..', 'server.py')
DATA = tempfile.mkdtemp(prefix='ct-demo-')
PEOPLE = [('Test Admin', 'admin', 'en', '111111'), ('Test Speaker', 'speaker', 'en', '222222'),
          ('Test Speaker ES', 'speaker', 'es', '333333'), ('Test Listener ES', 'listener', 'es', '444444'),
          ('Test Listener FR', 'listener', 'fr', '555555'), ('Test Listener SW', 'listener', 'sw', '666666')]


def check(cond, what):
    print(('ok   ' if cond else 'FAIL ') + what)
    if not cond:
        raise SystemExit(1)


def free_port():
    s = socket.socket(); s.bind(('127.0.0.1', 0)); p = s.getsockname()[1]; s.close(); return p


class Client:
    def __init__(self, port):
        self.port, self.cookie = port, None

    def req(self, method, path, body=None):
        c = http.client.HTTPConnection('127.0.0.1', self.port, timeout=10)
        h = {'X-VM': '1', 'Content-Type': 'application/json'}
        if self.cookie:
            h['Cookie'] = 'vm=' + self.cookie
        c.request(method, path, json.dumps(body).encode() if body is not None else None, h)
        r = c.getresponse()
        for k, v in r.getheaders():
            if k.lower() == 'set-cookie' and v.startswith('vm='):
                self.cookie = v.split(';')[0][3:]
        data = r.read()
        return r.status, (json.loads(data) if data else {})


def start(port):
    env = dict(os.environ, CTRANSLATE_FAKE_ENGINES='1')
    p = subprocess.Popen([sys.executable, SERVER, '--http', '--bind', '127.0.0.1', '--port', str(port),
                          '--data', DATA, '--demo'], env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    for _ in range(100):
        try:
            socket.create_connection(('127.0.0.1', port), 0.2).close()
            return p
        except OSError:
            time.sleep(0.1)
    raise SystemExit('server did not start')


def main():
    port = free_port()
    proc = start(port)
    try:
        who = {}
        for name, role, lang, pin in PEOPLE:
            c = Client(port)
            st, r = c.req('POST', '/api/login', {'name': name, 'pin': pin})
            check(st == 200 and r['me']['role'] == role and r['me']['lang'] == lang,
                  '%s signs in with %s as %s, %s' % (name, pin, role, lang))
            who[name] = c
        st, r = Client(port).req('POST', '/api/login', {'name': 'Test Speaker', 'pin': '999999'})
        check(st in (401, 403), 'a wrong PIN is refused')

        st, r = who['Test Admin'].req('GET', '/api/meetings')
        titles = sorted(m['title'] for m in r['meetings'])
        check(titles == ['Demo invited meeting', 'Demo training room'], 'two demo meetings')
        room = next(m for m in r['meetings'] if m['title'] == 'Demo training room')
        check(room['standing'] and room['access'] == 'anyone' and room['status'] == 'scheduled', 'the room is a standing QR room')
        st, r = who['Test Speaker ES'].req('GET', '/api/meetings/' + room['id'])
        check(r['meeting']['canHost'], 'Test Speaker ES can run the room')
        st, r = who['Test Listener FR'].req('GET', '/api/meetings')
        inv = [m for m in r['meetings'] if m['title'] == 'Demo invited meeting']
        check(inv and inv[0]['canJoin'] and not inv[0]['canHost'], 'Test Listener FR is invited, as a listener')
        st, r = who['Test Speaker ES'].req('GET', '/api/meetings')
        check(not any(m['title'] == 'Demo invited meeting' for m in r['meetings']), 'the invited meeting is hidden from others')

        g = Client(port)
        st, r = g.req('POST', '/api/guest', {'code': room['code'], 'lang': 'sw'})
        check(st == 200, 'a guest joins the room by its code')

        with open(os.path.join(DATA, 'demo-accounts.txt'), encoding='utf-8') as f:
            sheet = f.read()
        check(all(n in sheet and p in sheet for n, _, _, p in PEOPLE) and room['code'] in sheet,
              'demo-accounts.txt lists every account, PIN and the room code')

        st, r = who['Test Admin'].req('GET', '/api/people')
        tid = next(p['id'] for p in r['people'] if p['name'] == 'Test Speaker')
        st, r = who['Test Admin'].req('PUT', '/api/people/' + tid, {'pin': '7777777'})
        check(st == 200, 'a demo PIN changed')
    finally:
        proc.terminate(); proc.wait()

    proc = start(port)
    try:
        c = Client(port)
        st, r = c.req('POST', '/api/login', {'name': 'Test Speaker', 'pin': '222222'})
        check(st == 200, 'a second --demo start puts the PIN back')
        a = Client(port)
        st, r = a.req('POST', '/api/login', {'name': 'Test Admin', 'pin': '111111'})
        st, r = a.req('GET', '/api/meetings')
        check(len([m for m in r['meetings'] if m['title'].startswith('Demo ')]) == 2, 'and adds no meetings')
        st, r = a.req('GET', '/api/people')
        admins = sorted(p['name'] for p in r['people'] if p['role'] == 'admin')
        check(admins == ['Admin', 'Test Admin'], 'the first-run Admin is left alone beside Test Admin')
    finally:
        proc.terminate(); proc.wait()
        shutil.rmtree(DATA, ignore_errors=True)
    print('\nall demo checks passed')


if __name__ == '__main__':
    main()
