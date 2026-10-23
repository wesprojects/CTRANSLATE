#!/usr/bin/env python3
"""Drives tools/ctranslate-setup.sh against a sandbox (CTRANSLATE_SYSROOT) with stand-in
whiptail and systemctl, answering its dialogs from a script:

    python3 tests/setup_test.py

Checks: the status text, API keys written to ctranslate.env (and removed), a new admin
PIN that a real server then accepts, mismatched or short PINs refused, the network
file for DHCP and a fixed address (with the installer's file set aside, and put back
when netplan refuses). Exits non-zero on the first failure."""
import http.client, json, os, shutil, socket, subprocess, sys, tempfile, time

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.abspath(os.path.join(HERE, '..'))
SETUP = os.path.join(APP, 'tools', 'ctranslate-setup.sh')
FAILS = 0


def check(cond, what):
    global FAILS
    print(('ok   ' if cond else 'FAIL ') + what)
    if not cond:
        FAILS += 1
        raise SystemExit(1)


root = tempfile.mkdtemp(prefix='ctranslate-setup-')
binp = os.path.join(root, 'bin')
os.makedirs(binp)
answers = os.path.join(root, 'answers')
calls = os.path.join(root, 'calls')

# whiptail: each dialog takes the next line of the answers file. "CANCEL" makes it
# return 1 (Cancel/No); "YES" returns 0 with no output (yes/no, msgbox); anything
# else is printed to stderr as the dialog's answer.
open(os.path.join(binp, 'whiptail'), 'w').write('''#!/bin/bash
echo "whiptail $*" >> "%s"
a=$(head -1 "%s"); sed -i 1d "%s"
case "$*" in *--msgbox*|*--textbox*) [ -n "$CTRANSLATE_SHOW" ] && for f in "$@"; do [ -f "$f" ] && cat "$f" >> "$CTRANSLATE_SHOW"; done; exit 0 ;; esac
[ "$a" = CANCEL ] && exit 1
[ "$a" = YES ] && exit 0
printf '%%s' "$a" >&2
''' % (calls, answers, answers))
for tool, body in (('systemctl', 'echo "systemctl $*" >> "%s"; [ "$1" = is-active ] && echo active; exit 0' % calls),
                   ('netplan', 'echo "netplan $*" >> "%s"; [ "$1" = generate ] && [ -n "$NETPLAN_FAIL" ] && { echo bad >&2; exit 1; }; exit 0' % calls),
                   ('hostname', 'echo 192.168.50.20'), ('timedatectl', 'echo UTC'), ('clear', 'true'),
                   ('chown', 'true'), ('ip', 'echo')):
    open(os.path.join(binp, tool), 'w').write('#!/bin/bash\n' + body + '\n')
for f in os.listdir(binp):
    os.chmod(os.path.join(binp, f), 0o755)

# The sandbox: an installed CTRANSLATE with a data directory made by a real server.
sys_root = os.path.join(root, 'sys')
os.makedirs(sys_root + '/opt/ctranslate')
shutil.copy(os.path.join(APP, 'server.py'), sys_root + '/opt/ctranslate/server.py')
data = sys_root + '/var/lib/ctranslate'
os.makedirs(sys_root + '/etc/ctranslate')
os.makedirs(sys_root + '/etc/systemd/system')
open(sys_root + '/etc/systemd/system/ctranslate.service', 'w').write('ExecStart=/usr/bin/python3 server.py --port 8453 --tunnel-port 8454\n')
os.makedirs(sys_root + '/etc/netplan')
open(sys_root + '/etc/netplan/50-cloud-init.yaml', 'w').write('network: {version: 2}\n')
os.makedirs(sys_root + '/sys/class/net/enp1s0')
os.makedirs(sys_root + '/sys/class/net/wlp2s0')


def free_port():
    s = socket.socket(); s.bind(('127.0.0.1', 0)); p = s.getsockname()[1]; s.close(); return p


def start_server():
    port = free_port()
    p = subprocess.Popen([sys.executable, os.path.join(APP, 'server.py'), '--http', '--bind', '127.0.0.1',
                          '--port', str(port), '--data', data], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(100):
        try:
            socket.create_connection(('127.0.0.1', port), 0.2).close(); break
        except OSError:
            time.sleep(0.05)
    return p, port


def login(port, pin):
    c = http.client.HTTPConnection('127.0.0.1', port, timeout=5)
    c.request('POST', '/api/login', json.dumps({'name': 'Admin', 'pin': pin}), {'X-VM': '1', 'Content-Type': 'application/json'})
    return c.getresponse().status


def run(lines, *args, env=None):
    open(answers, 'w').write('\n'.join(lines) + '\n')
    open(calls, 'w').close()
    e = dict(os.environ, PATH=binp + ':' + os.environ['PATH'], CTRANSLATE_SYSROOT=sys_root, CTRANSLATE_NO_ROOT='1',
             CTRANSLATE_SHOW=os.path.join(root, 'shown'))
    e.update(env or {})
    open(e['CTRANSLATE_SHOW'], 'w').close()
    r = subprocess.run(['bash', SETUP] + list(args), env=e, capture_output=True, text=True, timeout=60)
    return r, open(calls).read(), open(e['CTRANSLATE_SHOW']).read()


try:
    srv, port = start_server()
    first_pin = open(data + '/admin-pin.txt').read().strip()
    check(login(port, first_pin) == 200, 'the first-run PIN signs in')
    srv.terminate(); srv.wait()

    r, _, _ = run([], '--status')
    check(r.returncode == 0 and 'https://192.168.50.20:8453/admin' in r.stdout and first_pin in r.stdout,
          'status names the admin page and the first PIN')

    # API keys: Google set, then Anthropic set, then Google removed.
    # The menu opens first: choose "keys", then the answers above.
    r, c, _ = run(['keys', 'GOOGLE_API_KEY', 'AIza-test-key ', 'ANTHROPIC_API_KEY', 'sk-ant-test', 'back', 'YES', 'shell'])
    env_text = open(sys_root + '/etc/ctranslate/ctranslate.env').read()
    check('GOOGLE_API_KEY=AIza-test-key\n' in env_text and 'ANTHROPIC_API_KEY=sk-ant-test\n' in env_text,
          'API keys written to ctranslate.env (spaces trimmed)')
    check(oct(os.stat(sys_root + '/etc/ctranslate/ctranslate.env').st_mode & 0o777) == '0o640', 'ctranslate.env is mode 0640')
    check('systemctl restart ctranslate' in c, 'CTRANSLATE restarted after the keys changed')
    r, c, _ = run(['keys', 'GOOGLE_API_KEY', '', 'back', 'YES', 'shell'])
    env_text = open(sys_root + '/etc/ctranslate/ctranslate.env').read()
    check('GOOGLE_API_KEY' not in env_text and 'ANTHROPIC_API_KEY=sk-ant-test' in env_text, 'an empty key removes it, the others stay')

    # Admin PIN: refused when the two differ or it is short; changed when right.
    r, c, shown = run(['pin', '123456', '654321', 'YES', 'shell'])
    check('two PINs differ' in c, 'mismatched PINs are refused with a message')
    srv, port = start_server()
    check(login(port, first_pin) == 200, 'mismatched PINs change nothing')
    srv.terminate(); srv.wait()
    r, c, _ = run(['pin', '12', 'YES', 'shell'])
    check('6 to 12 digits' in c and 'systemctl stop' not in c, 'a short PIN is refused before anything stops')
    r, c, _ = run(['pin', '90817263', '90817263', 'YES', 'shell'])
    check(r.returncode == 0 and 'systemctl stop ctranslate' in c and 'systemctl start ctranslate' in c, 'PIN change stops and starts the service')
    check(not os.path.exists(data + '/admin-pin.txt'), 'the first-run PIN file is removed once the PIN changes')
    srv, port = start_server()
    check(login(port, '90817263') == 200 and login(port, first_pin) == 401, 'the new PIN signs in; the old one no longer does')
    srv.terminate(); srv.wait()

    # Network: DHCP on the only wired port (the Wi-Fi one is not offered).
    r, c, _ = run(['network', 'dhcp', 'YES', 'YES', 'shell'])
    npf = sys_root + '/etc/netplan/01-ctranslate.yaml'
    check(os.path.exists(npf) and 'enp1s0:' in open(npf).read() and 'dhcp4: true' in open(npf).read(), 'DHCP written for the wired port')
    check(os.path.exists(sys_root + '/etc/netplan/50-cloud-init.yaml.ctranslate-off'), "the installer's network file is set aside")
    check(os.path.exists(sys_root + '/etc/cloud/cloud.cfg.d/99-ctranslate-network.cfg'), 'cloud-init stops writing the network')
    check('netplan apply' in c, 'netplan applied')
    # A fixed address.
    r, c, _ = run(['network', 'fixed', '192.168.50.30/24', '192.168.50.1', '192.168.50.1, 1.1.1.1', 'YES', 'YES', 'shell'])
    t = open(npf).read()
    check('addresses: [192.168.50.30/24]' in t and 'via: 192.168.50.1' in t and 'addresses: [192.168.50.1,1.1.1.1]' in t,
          'fixed address, gateway and DNS written')
    # A typo is refused before anything changes.
    before = open(npf).read()
    r, c, _ = run(['network', 'fixed', '192.168.50.300', 'YES', 'shell'])
    check(open(npf).read() == before and 'netplan' not in c, 'a mistyped address changes nothing')
    # netplan refusing puts the earlier files back.
    r, c, _ = run(['network', 'dhcp', 'YES', 'YES', 'shell'], env={'NETPLAN_FAIL': '1'})
    check(os.path.exists(npf) and open(npf).read() == before and not os.path.exists(npf + '.ctranslate-off'),
          'netplan refusing puts the earlier settings back')
    check('netplan apply' not in c, 'and nothing is applied')

    # The menu's own items exist and Exit leaves cleanly.
    r, c, _ = run(['CANCEL'])
    check(r.returncode == 0 and all(x in c for x in ('status', 'pin', 'network', 'timezone', 'tunnel', 'cert', 'keys', 'service', 'reboot', 'poweroff')),
          'the menu offers every item and Exit returns 0')
    print('\nall setup menu checks passed')
finally:
    shutil.rmtree(root, ignore_errors=True)
