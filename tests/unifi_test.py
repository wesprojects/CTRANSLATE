#!/usr/bin/env python3
"""UniFi Wi-Fi per meeting, against a stand-in console:

    python3 tests/unifi_test.py

The stand-in is an HTTPS server with its own self-signed certificate (openssl) that
answers /proxy/network/integration/v1/... as UniFi Network does, checks the API key, and
checks each Wi-Fi create body against the required fields of the official OpenAPI spec for
the version it reports (10.0.162, 10.1.84, 10.3.58; 10.6.106 adds two lock fields). It then
drives a real server: TEST UNIFI (certificate pinned), an instant meeting with Wi-Fi (made
with the right body), ending it (removed), a standing room (made again with the same name
and password), a new code (new password, made again), deleting a meeting, networks left
behind across a restart (removed; ones it did not make are left alone), a console whose
certificate changed (no request carries the key), an old Network version (refused), and an
error that a retry clears. Needs openssl. Exits non-zero on the first failure.

    python3 tests/unifi_test.py --serve     # the stand-in alone, for unifi_browser_test.js"""
import http.client, http.server, json, os, re, shutil, socket, ssl, subprocess, sys, tempfile, threading, time, uuid

HERE = os.path.dirname(os.path.abspath(__file__))
SERVER = os.path.join(HERE, '..', 'server.py')
TMP = tempfile.mkdtemp(prefix='ct-unifi-')
KEY = 'test-unifi-key-123'

REQUIRED = {  # IntegrationStandardWifiBroadcastCreateUpdateDto, per spec version
    (10, 0, 162): {'arpProxyEnabled', 'broadcastingFrequenciesGHz', 'bssTransitionEnabled', 'clientIsolationEnabled',
                   'enabled', 'hideName', 'multicastToUnicastConversionEnabled', 'name', 'securityConfiguration',
                   'type', 'uapsdEnabled'},
}
REQUIRED[(10, 1, 84)] = REQUIRED[(10, 0, 162)] | {'advertiseDeviceName'}
REQUIRED[(10, 3, 58)] = REQUIRED[(10, 1, 84)]
REQUIRED[(10, 6, 106)] = REQUIRED[(10, 3, 58)] | {'channel2gLockedTo6', 'dtimPeriod2gLockedTo3'}
KNOWN = REQUIRED[(10, 6, 106)] | {'network', 'broadcastingDeviceFilter', 'bandSteeringEnabled', 'mloEnabled'}


def check(cond, what):
    print(('ok   ' if cond else 'FAIL ') + what)
    if not cond:
        raise SystemExit(1)


def free_port():
    s = socket.socket(); s.bind(('127.0.0.1', 0)); p = s.getsockname()[1]; s.close(); return p


def make_cert(name):
    d = os.path.join(TMP, name); os.makedirs(d)
    subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '2', '-subj', '/CN=unifi',
                    '-keyout', d + '/k.pem', '-out', d + '/c.pem'], check=True, capture_output=True)
    return d + '/c.pem', d + '/k.pem'


class Console:
    """State of the stand-in console."""
    def __init__(self, version):
        self.version = version
        self.site = str(uuid.uuid4())
        self.vlan = str(uuid.uuid4())
        self.ap1, self.ap2 = str(uuid.uuid4()), str(uuid.uuid4())
        self.wifi = {}          # id -> body
        self.log = []           # (method, path, had_key)
        self.fail_next_create = None


def handler_for(con):
    class H(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def send(self, code, obj):
            data = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def err(self, code, msg):
            self.send(code, {'statusCode': code, 'statusName': 'ERR', 'code': 'api.err', 'message': msg,
                             'timestamp': '2026-10-23T00:00:00Z', 'requestPath': self.path, 'requestId': 'x'})

        def page(self, items):
            self.send(200, {'offset': 0, 'limit': 200, 'count': len(items), 'totalCount': len(items), 'data': items})

        def handle_any(self, method):
            con.log.append((method, self.path, self.headers.get('X-API-KEY') == KEY))
            if self.headers.get('X-API-KEY') != KEY:
                return self.err(401, 'Unauthorized')
            p = self.path.split('?')[0]
            if not p.startswith('/proxy/network/integration/v1'):
                return self.err(404, 'not found')
            p = p[len('/proxy/network/integration'):]
            v = '.'.join(map(str, con.version))
            if method == 'GET' and p == '/v1/info':
                return self.send(200, {'applicationVersion': v})
            if method == 'GET' and p == '/v1/sites':
                return self.page([{'id': con.site, 'internalReference': 'default', 'name': 'Default'}])
            m = re.fullmatch(r'/v1/sites/([^/]+)/(networks|devices|wifi/broadcasts)(?:/([^/]+))?', p)
            if not m or m.group(1) != con.site:
                return self.err(404, 'not found')
            what, item = m.group(2), m.group(3)
            if con.version < (10, 0, 162) and what == 'wifi/broadcasts':
                return self.err(404, 'not found')
            if method == 'GET' and what == 'networks':
                return self.page([{'id': str(uuid.uuid4()), 'name': 'Default', 'vlanId': 1, 'default': True, 'enabled': True,
                                   'management': 'GATEWAY', 'metadata': {}},
                                  {'id': con.vlan, 'name': 'Guests', 'vlanId': 30, 'default': False, 'enabled': True,
                                   'management': 'GATEWAY', 'metadata': {}}])
            if method == 'GET' and what == 'devices':
                return self.page([{'id': con.ap1, 'name': 'Line 3 AP', 'model': 'U7 Pro', 'features': ['accessPoint'], 'state': 'ONLINE'},
                                  {'id': con.ap2, 'name': 'Office AP', 'model': 'U6 Lite', 'features': ['accessPoint'], 'state': 'ONLINE'},
                                  {'id': str(uuid.uuid4()), 'name': 'Switch', 'model': 'USW', 'features': ['switching'], 'state': 'ONLINE'}])
            if what == 'wifi/broadcasts' and method == 'POST' and not item:
                n = int(self.headers.get('Content-Length') or 0)
                body = json.loads(self.rfile.read(n))
                need = REQUIRED[max(k for k in REQUIRED if k <= con.version)]
                missing = need - set(body)
                if missing:
                    return self.err(400, 'missing required fields: %s' % sorted(missing))
                extra = set(body) - KNOWN
                if extra or (con.version < (10, 6, 106) and {'channel2gLockedTo6', 'dtimPeriod2gLockedTo3'} & set(body)) \
                        or (con.version < (10, 1, 0) and 'advertiseDeviceName' in body):
                    return self.err(400, 'unknown fields for %s' % v)
                sc = body['securityConfiguration']
                if sc.get('type') != 'WPA2_PERSONAL' or not 8 <= len(sc.get('passphrase', '')) <= 63:
                    return self.err(400, 'bad security configuration')
                if any(b['name'] == body['name'] for b in con.wifi.values()):
                    return self.err(400, 'name taken')
                if con.fail_next_create:
                    msg, con.fail_next_create = con.fail_next_create, None
                    return self.err(500, msg)
                wid = str(uuid.uuid4())
                con.wifi[wid] = body
                return self.send(201, dict(body, id=wid, metadata={}))
            if what == 'wifi/broadcasts' and method == 'GET' and not item:
                return self.page([dict(b, id=k, metadata={}) for k, b in con.wifi.items()])
            if what == 'wifi/broadcasts' and method == 'GET' and item:
                if item not in con.wifi:
                    return self.err(404, 'no such broadcast')
                return self.send(200, dict(con.wifi[item], id=item, metadata={}))
            if what == 'wifi/broadcasts' and method == 'DELETE' and item:
                if item not in con.wifi:
                    return self.err(404, 'no such broadcast')
                del con.wifi[item]
                return self.send(200, {})
            return self.err(405, 'method not allowed')

        def do_GET(self):
            self.handle_any('GET')

        def do_POST(self):
            self.handle_any('POST')

        def do_DELETE(self):
            self.handle_any('DELETE')
    return H


def start_console(con, cert):
    srv = http.server.ThreadingHTTPServer(('127.0.0.1', 0), handler_for(con))
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER); ctx.load_cert_chain(*cert)
    srv.socket = ctx.wrap_socket(srv.socket, server_side=True)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, 'https://127.0.0.1:%d' % srv.server_address[1]


class App:
    def __init__(self, data):
        self.port = free_port()
        self.p = subprocess.Popen([sys.executable, SERVER, '--http', '--bind', '127.0.0.1', '--port', str(self.port), '--data', data],
                                  env=dict(os.environ, CTRANSLATE_FAKE_ENGINES='1'), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        for _ in range(100):
            try:
                socket.create_connection(('127.0.0.1', self.port), 0.2).close(); break
            except OSError:
                time.sleep(0.05)
        self.cookie = None

    def req(self, method, path, body=None):
        c = http.client.HTTPConnection('127.0.0.1', self.port, timeout=30)
        h = {'X-VM': '1', 'Content-Type': 'application/json'}
        if self.cookie:
            h['Cookie'] = 'vm=' + self.cookie
        c.request(method, path, json.dumps(body) if body is not None else None, h)
        r = c.getresponse()
        for k, v in r.getheaders():
            if k.lower() == 'set-cookie' and v.startswith('vm='):
                self.cookie = v.split(';')[0][3:]
        return r.status, json.loads(r.read() or b'{}')

    def login(self, data):
        pin = open(os.path.join(data, 'admin-pin.txt')).read().strip()
        return self.req('POST', '/api/login', {'name': 'Admin', 'pin': pin})

    def wait_wifi(self, mid, state, secs=8):
        end = time.time() + secs
        while time.time() < end:
            st, r = self.req('GET', '/api/meetings/' + mid)
            if r.get('meeting', {}).get('wifiState') == state:
                return r['meeting']
            time.sleep(0.15)
        return self.req('GET', '/api/meetings/' + mid)[1].get('meeting')

    def stop(self):
        self.p.terminate()
        try:
            return self.p.communicate(timeout=5)[0]
        except Exception:
            return ''


def wait(pred, secs=8):
    end = time.time() + secs
    while time.time() < end:
        if pred():
            return True
        time.sleep(0.1)
    return pred()


def main():
    cert1 = make_cert('a')
    con = Console((10, 3, 58))
    srv, url = start_console(con, cert1)
    data = os.path.join(TMP, 'data')
    app = App(data)
    try:
        app.login(data)
        st, r = app.req('POST', '/api/meetings', {'title': 'Before setup', 'wifi': True})
        check(st == 400 and 'UniFi is not set up' in r.get('error', ''), 'Wi-Fi refused before UniFi is set up')

        st, r = app.req('POST', '/api/settings/unifi-test', {'url': url + '/x', 'key': KEY})
        check(st == 400, 'a console address with a path is refused')
        st, r = app.req('POST', '/api/settings/unifi-test', {'url': url, 'key': 'wrong'})
        check(st == 502 and 'refused' in r.get('error', ''), 'a wrong API key is reported')
        st, r = app.req('POST', '/api/settings/unifi-test', {'url': url, 'key': KEY})
        check(st == 200 and r['unifi']['version'] == '10.3.58', 'TEST UNIFI reads the version')
        site = r['unifi']['sites'][0]
        check(r['site'] == con.site, 'the only site is picked')
        check([a['name'] for a in site['aps']] == ['Line 3 AP', 'Office AP'], 'access points listed (switches left out)')
        check(any(n['name'] == 'Guests' and n['vlanId'] == 30 for n in site['networks']), 'networks listed with VLAN')
        st, s = app.req('GET', '/api/settings')
        check(s['settings']['unifiKeySet'] and len(s['settings']['unifiFingerprint']) == 64, 'key saved, certificate pinned')
        check('unifiKey' not in s['settings'], 'the key is never sent back')
        st, s = app.req('PUT', '/api/settings', {'unifiNetwork': con.vlan, 'unifiAps': [con.ap1]})
        check(st == 200, 'guest VLAN and one access point chosen')
        st, cfg = app.req('GET', '/api/config')
        check(cfg['unifi'] is True, 'the apps are told UniFi is on')

        # Instant meeting with Wi-Fi.
        st, r = app.req('POST', '/api/meetings', {'instant': True, 'title': 'Line 3 safety', 'wifi': True, 'standing': True})
        m = r['meeting']; mid = m['id']
        check(st == 200 and m['wifiName'] == 'CT Line 3 safety' and len(m['wifiPass']) == 12, 'meeting gets a Wi-Fi name and password')
        m = app.wait_wifi(mid, 'up')
        check(m['wifiState'] == 'up', 'Wi-Fi is up while the meeting is live')
        check(len(con.wifi) == 1, 'one network made on the console')
        body = list(con.wifi.values())[0]
        check(body['name'] == 'CT Line 3 safety' and body['securityConfiguration']['passphrase'] == m['wifiPass'],
              'made with the meeting\'s name and password')
        check(body['network'] == {'type': 'SPECIFIC', 'networkId': con.vlan}, 'on the chosen VLAN')
        check(body['broadcastingDeviceFilter'] == {'type': 'DEVICES', 'deviceIds': [con.ap1]}, 'only on the chosen access point')
        check(body['clientIsolationEnabled'] is True and body['securityConfiguration']['type'] == 'WPA2_PERSONAL',
              'WPA2 with phones isolated from one another')
        check('channel2gLockedTo6' not in body, 'no 10.6 fields on a 10.3 console')
        first_pass = m['wifiPass']
        check(m['wifiQr'] == 'WIFI:T:WPA;S:CT Line 3 safety;P:%s;;' % first_pass, 'hosts get the join-the-Wi-Fi QR text')

        # A listener (guest) never sees the Wi-Fi password.
        st, info = app.req('GET', '/api/code/' + m['code'])
        guest = App.__new__(App); guest.port = app.port; guest.cookie = None
        st, g = guest.req('POST', '/api/guest', {'code': m['code'], 'lang': 'es'})
        st, gm = guest.req('GET', '/api/meetings/' + mid)
        check(gm['meeting']['wifi'] is True and 'wifiPass' not in gm['meeting'] and 'wifiQr' not in gm['meeting'],
              'guests do not get the Wi-Fi password')

        # End the session: removed. Start again: same name and password.
        app.req('POST', '/api/meetings/%s/end' % mid)
        check(wait(lambda: not con.wifi), 'ending the session removes the network')
        app.req('POST', '/api/meetings/%s/start' % mid)
        m = app.wait_wifi(mid, 'up')
        b2 = list(con.wifi.values())[0] if con.wifi else {}
        check(b2.get('name') == 'CT Line 3 safety' and b2['securityConfiguration']['passphrase'] == first_pass,
              'a standing room\'s next session has the same Wi-Fi, so the printed QR still works')

        # New code: new password, network made again.
        st, r = app.req('POST', '/api/meetings/%s/newcode' % mid)
        new_pass = r['meeting']['wifiPass']
        check(new_pass != first_pass, 'a new code gives a new Wi-Fi password')
        check(wait(lambda: len(con.wifi) == 1 and list(con.wifi.values())[0]['securityConfiguration']['passphrase'] == new_pass),
              'the network is made again with it, and the old one is gone')

        # A second meeting, then deleted while live.
        st, r = app.req('POST', '/api/meetings', {'instant': True, 'title': 'Office huddle', 'wifi': True})
        m2 = r['meeting']
        app.wait_wifi(m2['id'], 'up')
        check(len(con.wifi) == 2, 'two meetings, two networks')
        app.req('DELETE', '/api/meetings/' + m2['id'])
        check(wait(lambda: len(con.wifi) == 1), 'deleting a meeting removes its network')

        # Turning Wi-Fi off on a live meeting removes it; on again makes it.
        app.req('PUT', '/api/meetings/' + mid, {'wifi': False})
        check(wait(lambda: not con.wifi), 'Wi-Fi turned off on a live meeting: removed')
        app.req('PUT', '/api/meetings/' + mid, {'wifi': True})
        check(app.wait_wifi(mid, 'up')['wifiState'] == 'up' and len(con.wifi) == 1, 'and on again: made')

        # An error is shown, and TRY AGAIN clears it.
        app.req('PUT', '/api/meetings/' + mid, {'wifi': False})
        wait(lambda: not con.wifi)
        con.fail_next_create = 'out of SSID slots'
        app.req('PUT', '/api/meetings/' + mid, {'wifi': True})
        m = app.wait_wifi(mid, 'error')
        check(m['wifiState'] == 'error' and 'out of SSID slots' in m['wifiError'], 'a UniFi error is shown on the meeting')
        app.req('POST', '/api/meetings/%s/wifi' % mid)
        check(app.wait_wifi(mid, 'up')['wifiState'] == 'up', 'TRY AGAIN makes it')

        # Restart with a network left behind (made by this server) and one made by someone else.
        foreign = str(uuid.uuid4()); con.wifi[foreign] = {'name': 'Staff Wi-Fi'}
        leftover = str(uuid.uuid4()); con.wifi[leftover] = {'name': 'CT Old meeting'}
        out = app.stop()
        sp = os.path.join(data, 'settings.json'); st_ = json.load(open(sp)); st_['unifiOwned'].append(leftover); json.dump(st_, open(sp, 'w'))
        app = App(data); app.login(data)
        check(wait(lambda: leftover not in con.wifi), 'after a restart, a network it made and no meeting holds is removed')
        check(foreign in con.wifi, 'a network it did not make is never touched')
        check(app.wait_wifi(mid, 'up')['wifiState'] == 'up' and sum(1 for b in con.wifi.values() if b['name'].startswith('CT Line')) == 1,
              'the live meeting keeps exactly one network')

        # The console's certificate changes: nothing is sent with the key.
        srv.shutdown(); srv.server_close()
        con.log.clear()
        srv, url2 = start_console(con, make_cert('b'))
        port2 = url2.rsplit(':', 1)[1]
        # Same address, new certificate: point the saved address at the new port without a new TEST.
        st_ = json.load(open(sp))
        out = app.stop()
        st_ = json.load(open(sp)); st_['unifiUrl'] = url2; json.dump(st_, open(sp, 'w'))
        app = App(data); app.login(data)
        m = app.wait_wifi(mid, 'error')
        check(m['wifiState'] == 'error' and 'certificate' in m['wifiError'], 'a changed certificate is refused')
        check(not con.log, 'and no request reached the console with the key')
        st, r = app.req('POST', '/api/settings/unifi-test', {})
        check(st == 200, 'TEST UNIFI trusts the new certificate')
        app.req('POST', '/api/meetings/%s/wifi' % mid)
        check(app.wait_wifi(mid, 'up')['wifiState'] == 'up', 'and Wi-Fi works again')
        # Someone deletes the network by hand in UniFi: it is made again.
        for wid in [k for k, v in con.wifi.items() if v['name'].startswith('CT Line')]:
            del con.wifi[wid]
        app.req('POST', '/api/meetings/%s/wifi' % mid)
        check(wait(lambda: any(v['name'].startswith('CT Line') for v in con.wifi.values())),
              'a network deleted by hand in UniFi is made again')
        app.req('POST', '/api/meetings/%s/end' % mid)
        wait(lambda: not any(b['name'].startswith('CT Line') for b in con.wifi.values()))
        app.stop()

        # Versions: 10.0.162 (no advertiseDeviceName), 10.6.106 (lock fields), 9.x refused.
        for ver, has_adv, has_lock in (((10, 0, 162), False, False), ((10, 6, 106), True, True)):
            c2 = Console(ver); s2, u2 = start_console(c2, cert1)
            d2 = os.path.join(TMP, 'data-%d-%d' % ver[:2]); a2 = App(d2); a2.login(d2)
            st, r = a2.req('POST', '/api/settings/unifi-test', {'url': u2, 'key': KEY})
            st, r = a2.req('POST', '/api/meetings', {'instant': True, 'title': 'Version check', 'wifi': True})
            mm = a2.wait_wifi(r['meeting']['id'], 'up')
            b = list(c2.wifi.values())[0] if c2.wifi else {}
            check(mm['wifiState'] == 'up' and ('advertiseDeviceName' in b) == has_adv and ('channel2gLockedTo6' in b) == has_lock,
                  'UniFi Network %s: the body has the fields that version requires' % '.'.join(map(str, ver)))
            a2.stop(); s2.shutdown()
        c3 = Console((9, 5, 21)); s3, u3 = start_console(c3, cert1)
        d3 = os.path.join(TMP, 'data-9'); a3 = App(d3); a3.login(d3)
        st, r = a3.req('POST', '/api/settings/unifi-test', {'url': u3, 'key': KEY})
        check(st == 502 and '10.0.162' in r.get('error', ''), 'UniFi Network 9.x is refused with the version needed')
        a3.stop(); s3.shutdown()
        print('\nall UniFi checks passed')
    finally:
        try:
            app.stop()
        except Exception:
            pass
        shutil.rmtree(TMP, ignore_errors=True)


def serve():
    # --serve: one stand-in console (10.3.58) for tests/unifi_browser_test.js. Prints one JSON
    # line with its address, key and ids, then serves until killed.
    con = Console((10, 3, 58))
    srv, url = start_console(con, make_cert('serve'))
    print(json.dumps({'url': url, 'key': KEY, 'site': con.site, 'vlan': con.vlan, 'ap1': con.ap1}), flush=True)
    try:
        while True:
            time.sleep(3600)
    finally:
        shutil.rmtree(TMP, ignore_errors=True)


if __name__ == '__main__':
    serve() if sys.argv[1:] == ['--serve'] else main()
