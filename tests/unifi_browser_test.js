// The UniFi screens in Chromium (Playwright), against the stand-in console of
// tests/unifi_test.py (--serve), fake engines, no network:
//
//   NODE_PATH=$(npm root -g) node tests/unifi_browser_test.js [screenshot-dir]
//
// An admin types the console's address and key, presses TEST UNIFI, picks the
// guest VLAN and one access point and saves; a speaker makes an instant QR
// meeting with Wi-Fi; the share sheet shows the network's code, the console
// says the Wi-Fi is on, the printed sheet has two codes (the Wi-Fi one reads
// back as WIFI:T:WPA;... when OpenCV is installed) and the second-step line in
// each language; ending the meeting removes the network from the console.
// Any script error on any page fails the test.
const {chromium} = require('playwright');
const {spawn, spawnSync} = require('child_process');
const fs = require('fs'), os = require('os'), path = require('path'), net = require('net'), https = require('https'), readline = require('readline');

const SHOTS = process.argv[2] || '';
function freePort() {
  return new Promise(res => { const s = net.createServer(); s.listen(0, '127.0.0.1', () => { const p = s.address().port; s.close(() => res(p)); }); });
}
let failed = false;
function check(cond, what) { console.log((cond ? 'ok   ' : 'FAIL ') + what); if (!cond) { failed = true; throw new Error(what); } }
const sleep = ms => new Promise(r => setTimeout(r, ms));

(async () => {
  // The stand-in console.
  const con = spawn('python3', [path.join(__dirname, 'unifi_test.py'), '--serve'], {stdio: ['ignore', 'pipe', 'inherit']});
  const info = await new Promise((res, rej) => {
    readline.createInterface({input: con.stdout}).once('line', l => res(JSON.parse(l)));
    con.once('exit', c => rej(new Error('stand-in console exited ' + c)));
  });
  const broadcasts = () => new Promise((res, rej) => {
    const u = new URL(info.url + '/proxy/network/integration/v1/sites/' + info.site + '/wifi/broadcasts');
    https.get({host: u.hostname, port: u.port, path: u.pathname, rejectUnauthorized: false, headers: {'X-API-KEY': info.key}}, r => {
      let b = ''; r.on('data', d => b += d); r.on('end', () => res(JSON.parse(b).data));
    }).on('error', rej);
  });

  const port = await freePort(), base = 'http://127.0.0.1:' + port;
  const data = fs.mkdtempSync(path.join(os.tmpdir(), 'ctranslate-unifi-browser-'));
  const srv = spawn('python3', [path.join(__dirname, '..', 'server.py'), '--http', '--bind', '127.0.0.1', '--port', String(port), '--data', data],
    {env: Object.assign({}, process.env, {CTRANSLATE_FAKE_ENGINES: '1'}), stdio: ['ignore', 'pipe', 'pipe']});
  let log = ''; srv.stdout.on('data', d => log += d); srv.stderr.on('data', d => log += d);
  for (let i = 0; i < 100 && !fs.existsSync(path.join(data, 'admin-pin.txt')); i++) await sleep(50);
  await sleep(300);
  const pin = fs.readFileSync(path.join(data, 'admin-pin.txt'), 'utf8').trim();
  const api = async (cookie, method, p, body) => {
    const r = await fetch(base + '/api/' + p, {method, headers: {'X-VM': '1', 'Content-Type': 'application/json', Cookie: cookie || ''}, body: body ? JSON.stringify(body) : undefined});
    const sc = r.headers.get('set-cookie'); return {status: r.status, json: await r.json(), cookie: sc ? sc.split(';')[0] : cookie};
  };
  const a = await api('', 'POST', 'login', {name: 'Admin', pin});
  await api(a.cookie, 'POST', 'people', {name: 'Maria', role: 'speaker', lang: 'en', pin: '246810'});

  const browser = await chromium.launch();
  const errors = [];
  const watch = (page, name) => {
    page.on('pageerror', e => errors.push(name + ': ' + e.message));
    page.on('console', m => { if (m.type() === 'error' && !/Failed to load resource|audio|media|NotSupportedError/i.test(m.text())) errors.push(name + ' console: ' + m.text()); });
  };
  const shot = async (page, name) => { if (SHOTS) await page.screenshot({path: path.join(SHOTS, name + '.png'), fullPage: true}); };

  try {
    // ---- admin sets up UniFi ----
    const adCtx = await browser.newContext({viewport: {width: 1280, height: 900}});
    const ad = await adCtx.newPage(); watch(ad, 'admin');
    await ad.goto(base + '/admin');
    await ad.fill('#vm-name', 'Admin'); await ad.fill('#vm-pin', pin);
    await ad.click('#vm-signin button[type=submit]');
    await ad.waitForSelector('#m-list');
    await ad.click('[data-tab=settings]');
    await ad.waitForSelector('#u-test');
    check((await ad.textContent('#u-state')).includes('Not set up'), 'settings: UniFi not set up');
    await ad.fill('#u-url', info.url); await ad.fill('#k-unifi', 'wrong-key');
    await ad.click('#u-test');
    await ad.waitForFunction(() => document.getElementById('u-err').textContent);
    check((await ad.textContent('#u-err')).includes('refused'), 'a wrong key is reported');
    await ad.fill('#k-unifi', info.key);
    await ad.click('#u-test');
    await ad.waitForSelector('#u-net option', {state: 'attached'});
    check((await ad.textContent('#u-state')).includes('UniFi Network 10.3.58'), 'TEST UNIFI: connected, version shown');
    const nets = await ad.$$eval('#u-net option', ns => ns.map(n => n.textContent));
    check(nets.some(t => t.includes('Guests') && t.includes('VLAN 30')), 'networks listed with their VLAN');
    const aps = await ad.$$eval('#u-aps label', ns => ns.map(n => n.textContent));
    check(aps.length === 2 && aps.includes('Line 3 AP'), 'access points listed, the switch left out');
    await ad.selectOption('#u-net', info.vlan);
    await ad.check('input[name=uap][value="' + info.ap1 + '"]');
    await shot(ad, 'u1-admin-unifi');
    await ad.click('#s-save');
    await ad.waitForFunction(() => /Ready/.test((document.getElementById('u-state') || {}).textContent || ''));
    check(true, 'saved: Ready, meetings can make Wi-Fi networks');
    const s = (await api(a.cookie, 'GET', 'settings')).json.settings;
    check(s.unifiNetwork === info.vlan && s.unifiAps.length === 1 && s.unifiAps[0] === info.ap1 && s.unifiKeySet, 'VLAN, access point and key stored');

    // ---- a speaker makes an instant meeting with Wi-Fi ----
    const spCtx = await browser.newContext({viewport: {width: 1280, height: 860}});
    const sp = await spCtx.newPage(); watch(sp, 'speak');
    await sp.goto(base + '/speak');
    await sp.fill('#vm-name', 'Maria'); await sp.fill('#vm-pin', '246810');
    await sp.click('#vm-signin button[type=submit]');
    await sp.waitForSelector('#h-instant');
    await sp.click('#h-instant');
    check(await sp.isChecked('#i-wifi'), 'the instant dialog offers Wi-Fi, ticked');
    await sp.fill('#i-title', 'Line 3 safety');
    await sp.click('#i-go');
    await sp.waitForSelector('.modal .qr svg');
    check((await sp.textContent('.modal')).includes('CT Line 3 safety'), 'share sheet names the meeting Wi-Fi');
    check((await sp.$$('.modal .qr svg')).length === 2, 'share sheet has the meeting code and the Wi-Fi code');
    await shot(sp, 'u2-share');
    await sp.click('.modal [data-close]');
    for (let i = 0; i < 40 && !/is on/.test(await sp.textContent('#c-wifi')); i++) await sleep(500);
    check((await sp.textContent('#c-wifi')).includes('is on'), 'console: Wi-Fi is on');
    const made = await broadcasts();
    check(made.length === 1 && made[0].name === 'CT Line 3 safety' && made[0].network.networkId === info.vlan, 'the console has the network, on the VLAN');
    await shot(sp, 'u3-console');

    // ---- the printed sheet ----
    const mid = new URL(sp.url()).searchParams.get('m');
    const pr = await spCtx.newPage(); watch(pr, 'print');
    await pr.goto(base + '/print?m=' + mid);
    await pr.waitForSelector('.two .qr svg');
    check((await pr.$$('.two .qr svg')).length === 2, 'QR sheet: two codes, Wi-Fi first');
    const lines = await pr.$$eval('.line span', ns => ns.map(n => n.textContent));
    check(lines.length === 4 && lines[0].startsWith('Scan 1') && lines.some(t => t.startsWith('Escanee 1')) && lines.some(t => t.startsWith('Changanua 1')),
      'QR sheet: two-step line in English, Spanish, French, Swahili');
    const pass = (await api(a.cookie, 'GET', 'meetings/' + mid)).json.meeting.wifiPass;
    check((await pr.textContent('.two')).includes(pass), 'QR sheet prints the password');
    await shot(pr, 'u4-print');
    if (spawnSync('python3', ['-c', 'import cv2']).status === 0) {
      const png = path.join(data, 'wifi-qr.png');
      const el = await pr.$('.two > div:first-child .qr');
      await el.screenshot({path: png});
      const r = spawnSync('python3', ['-c', 'import cv2,sys; print(cv2.QRCodeDetector().detectAndDecode(cv2.imread(sys.argv[1]))[0])', png], {encoding: 'utf8'});
      check(r.stdout.trim() === 'WIFI:T:WPA;S:CT Line 3 safety;P:' + pass + ';;', 'the printed Wi-Fi code reads back: ' + r.stdout.trim());
    } else console.log('SKIP the printed Wi-Fi code read back (no OpenCV)');

    // ---- admin list, then end ----
    await ad.click('[data-tab=meetings]');
    await ad.waitForSelector('#m-list .item');
    check((await ad.textContent('#m-list')).includes('Wi-Fi “CT Line 3 safety” is on'), 'admin meetings list shows the Wi-Fi');
    await api(a.cookie, 'POST', 'meetings/' + mid + '/end');
    let left = 1;
    for (let i = 0; i < 40 && left; i++) { left = (await broadcasts()).length; if (left) await sleep(250); }
    check(left === 0, 'ending the meeting removes the network');

    check(errors.length === 0, 'no script errors on any page' + (errors.length ? ':\n  ' + errors.join('\n  ') : ''));
    console.log('\nall UniFi browser checks passed');
  } catch (e) {
    if (!failed) console.log('FAIL ' + e.message);
    if (errors.length) console.log('page errors:\n  ' + errors.join('\n  '));
    console.log('--- server log ---\n' + log.slice(-2000));
    process.exitCode = 1;
  } finally {
    await browser.close();
    srv.kill(); con.kill();
    fs.rmSync(data, {recursive: true, force: true});
  }
})();
