// Drives the real pages in Chromium (Playwright), fake engines, no network:
//
//   NODE_PATH=$(npm root -g) node tests/browser_test.js [screenshot-dir]
//
// A speaker on a PC signs in, starts an instant QR meeting and opens the QR
// sheet; a listener on an emulated iPhone opens the QR link, picks Español,
// taps ESCUCHAR, and reads the phrase the speaker sends in Spanish; the
// speaker ends the session and the listener is told, in Spanish, to wait.
// The admin pages render. Any script error on any page fails the test.
const {chromium, devices} = require('playwright');
const {spawn} = require('child_process');
const fs = require('fs'), os = require('os'), path = require('path'), net = require('net');

const SHOTS = process.argv[2] || '';

function freePort() {
  return new Promise(res => { const s = net.createServer(); s.listen(0, '127.0.0.1', () => { const p = s.address().port; s.close(() => res(p)); }); });
}
let failed = false;
function check(cond, what) { console.log((cond ? 'ok   ' : 'FAIL ') + what); if (!cond) { failed = true; throw new Error(what); } }

(async () => {
  const port = await freePort(), base = 'http://127.0.0.1:' + port;
  const data = fs.mkdtempSync(path.join(os.tmpdir(), 'ctranslate-browser-'));
  const srv = spawn('python3', [path.join(__dirname, '..', 'server.py'), '--http', '--bind', '127.0.0.1', '--port', String(port), '--data', data],
    {env: Object.assign({}, process.env, {CTRANSLATE_FAKE_ENGINES: '1'}), stdio: ['ignore', 'pipe', 'pipe']});
  let log = ''; srv.stdout.on('data', d => log += d); srv.stderr.on('data', d => log += d);
  for (let i = 0; i < 100 && !fs.existsSync(path.join(data, 'admin-pin.txt')); i++) await new Promise(r => setTimeout(r, 50));
  await new Promise(r => setTimeout(r, 300));
  const pin = fs.readFileSync(path.join(data, 'admin-pin.txt'), 'utf8').trim();

  // Admin setup through the API.
  const api = async (cookie, method, p, body) => {
    const r = await fetch(base + '/api/' + p, {method, headers: {'X-VM': '1', 'Content-Type': 'application/json', Cookie: cookie || ''}, body: body ? JSON.stringify(body) : undefined});
    const sc = r.headers.get('set-cookie'); return {status: r.status, json: await r.json(), cookie: sc ? sc.split(';')[0] : cookie};
  };
  const a = await api('', 'POST', 'login', {name: 'Admin', pin});
  await api(a.cookie, 'PUT', 'settings', {anthropicKey: 'test', googleKey: 'test', serverStt: true, freeVoice: true, ttsEngine: 'google', mtEngine: 'claude'});
  await api(a.cookie, 'POST', 'people', {name: 'Maria', role: 'speaker', lang: 'en', pin: '246810'});

  const browser = await chromium.launch({args: ['--use-fake-ui-for-media-stream', '--use-fake-device-for-media-stream', '--autoplay-policy=no-user-gesture-required']});
  const errors = [];
  const watch = (page, name) => {
    page.on('pageerror', e => errors.push(name + ': ' + e.message));
    page.on('console', m => { if (m.type() === 'error' && !/Failed to load resource|audio|media|NotSupportedError/i.test(m.text())) errors.push(name + ' console: ' + m.text()); });
  };
  const shot = async (page, name) => { if (SHOTS) await page.screenshot({path: path.join(SHOTS, name + '.png')}); };

  try {
    // ---- speaker on a PC ----
    const spCtx = await browser.newContext({viewport: {width: 1280, height: 860}, permissions: ['microphone']});
    const sp = await spCtx.newPage(); watch(sp, 'speak');
    await sp.goto(base + '/speak');
    await sp.fill('#vm-name', 'Maria'); await sp.fill('#vm-pin', '246810');
    await sp.click('#vm-signin button[type=submit]');
    await sp.waitForSelector('#h-instant');
    check(await sp.isVisible('#h-instant'), 'speaker signs in and sees INSTANT QR MEETING');
    await sp.click('#h-instant');
    await sp.fill('#i-title', 'Line 3 safety briefing');
    await sp.check('#i-standing');
    await sp.click('#i-go');
    await sp.waitForSelector('.modal .qr svg');
    check(true, 'instant meeting opens its QR share sheet');
    await shot(sp, '1-speaker-qr');
    const code = (await sp.textContent('.modal .code-big')).trim();
    check(/^[A-Z2-9]{6}$/.test(code), 'meeting code ' + code);
    await sp.click('.modal [data-close]');
    check(await sp.isVisible('#mic'), 'speaker console shows the microphone');
    await shot(sp, '2-speaker-console');

    // ---- the printable sheet ----
    const mid = new URL(sp.url()).searchParams.get('m');
    const pr = await spCtx.newPage(); watch(pr, 'print');
    await pr.goto(base + '/print?m=' + mid);
    await pr.waitForSelector('.sheet .qr svg');
    const lines = await pr.$$eval('.line span', ns => ns.map(n => n.textContent));
    check(lines.length === 4 && lines.some(t => t.startsWith('Escanee')) && lines.some(t => t.startsWith('Changanua')), 'QR sheet: scan line in English, Spanish, French, Swahili');
    await shot(pr, '3-print-sheet');

    // ---- listener on an iPhone, by the QR link ----
    const lsCtx = await browser.newContext(Object.assign({}, devices['iPhone 13'], {locale: 'es-MX'}));
    const ls = await lsCtx.newPage(); watch(ls, 'listen');
    await ls.goto(base + '/j/' + code);
    await ls.waitForSelector('.langbtn');
    const langs = await ls.$$eval('.langbtn', ns => ns.map(n => n.firstChild.textContent));
    check(JSON.stringify(langs) === JSON.stringify(['English', 'Español', 'Français', 'Kiswahili']), 'first screen: choose a language (' + langs.join(', ') + ')');
    check(await ls.$eval('.langbtn.suggest', n => n.firstChild.textContent) === 'Español', 'the phone’s own language (es-MX) is suggested');
    await shot(ls, '4-listener-language');
    await ls.click('.langbtn:nth-child(2)');
    await ls.waitForSelector('#play');
    check((await ls.textContent('#play')).includes('ESCUCHAR'), 'join screen is in Spanish: ESCUCHAR');
    await shot(ls, '5-listener-join');
    await ls.click('#play');
    await ls.waitForSelector('#room:not([hidden])');
    check((await ls.textContent('#leave')) === 'SALIR', 'in the meeting, buttons are in Spanish');

    // The speaker says something (the page's own sender; the browser recognizer is not in headless Chromium).
    await sp.bringToFront();
    await sp.evaluate(() => Send.final('Good morning, everyone. Please wear your safety glasses today.'));
    await ls.waitForSelector('.phrase .txt');
    const said = await ls.textContent('.phrase.last .txt');
    check(said.startsWith('[claude es] Good morning'), 'listener reads the phrase in Spanish: ' + said);
    await sp.waitForSelector('.phrase.mine .trs');
    check((await sp.textContent('.phrase.mine .trs')).includes('ES'), 'speaker sees what each language received');
    await ls.waitForFunction(() => P.el.src.includes('/api/audio/') || P.current === null, null, {timeout: 5000});
    check(true, 'listener’s player picked up the voice clip');
    await shot(ls, '6-listener-room');

    // Live words while still speaking.
    await sp.evaluate(() => { Send.interim('and the forklifts'); });
    await ls.waitForSelector('#liveline:not([hidden])');
    check((await ls.textContent('#live-txt')).includes('forklifts'), 'live captions while the speaker is still talking');

    // Microphone: tap starts; a short press does nothing; only a 1.5 s hold stops it.
    // (Google recognizer with Chromium's fake microphone: the browser recognizer needs a real device.)
    await sp.selectOption('#s-engine', 'server');
    check(await sp.$eval('#s-mode', n => n.value) === 'lock', 'default talk mode: tap to start, hold to stop');
    await sp.click('#mic');
    await sp.waitForFunction(() => document.getElementById('mic-label').textContent.includes('HOLD TO STOP'));
    check(true, 'a tap starts the microphone');
    await sp.hover('#mic'); await sp.mouse.down(); await sp.waitForTimeout(400); await sp.mouse.up();
    await sp.waitForTimeout(300);
    check(await sp.$eval('#mic', n => n.classList.contains('on')), 'a brush or short press does not stop it');
    await sp.mouse.down(); await sp.waitForTimeout(700);
    check(await sp.$eval('#mic', n => n.classList.contains('holding')), 'holding shows the filling ring');
    await shot(sp, '2b-speaker-holding');
    await sp.waitForTimeout(1100); await sp.mouse.up();
    await sp.waitForTimeout(800);
    check(!(await sp.$eval('#mic', n => n.classList.contains('on'))) && (await sp.textContent('#mic-label')) === 'TAP TO START', 'holding 1.5 s stops it, and the release does not restart it');
    const heard = await ls.$$eval('.phrase .txt', ns => ns.map(n => n.textContent).join('|'));
    check(heard.includes('spoken phrase'), 'what the microphone heard reached the listener');

    // End the session (standing room): the listener waits, in Spanish.
    await sp.click('#c-end');
    await sp.click('#vm-yes');
    await ls.waitForSelector('#ov:not([hidden])');
    check((await ls.textContent('#ov-msg')).startsWith('Esperando'), 'after the session ends the listener waits, in Spanish');
    await sp.waitForSelector('#c-start');
    await sp.click('#c-start');
    await ls.waitForSelector('#ov', {state: 'hidden'});
    check(true, 'next session starts and the waiting listener is back in');

    // ---- admin ----
    const adCtx = await browser.newContext({viewport: {width: 1280, height: 900}});
    const ad = await adCtx.newPage(); watch(ad, 'admin');
    await ad.goto(base + '/admin');
    await ad.fill('#vm-name', 'Admin'); await ad.fill('#vm-pin', pin);
    await ad.click('#vm-signin button[type=submit]');
    await ad.waitForSelector('#m-list .item');
    check((await ad.textContent('#m-list')).includes('Line 3 safety briefing'), 'admin sees the meeting');
    await shot(ad, '7-admin-meetings');
    await ad.click('[data-tab=settings]');
    await ad.waitForSelector('#s-save');
    check(await ad.$eval('#s-model', n => n.value) === 'claude-haiku-5-5', 'admin settings: Claude Haiku 5.5 selected');
    check(await ad.$eval('#s-tier', n => n.value) === 'wavenet', 'admin settings: WaveNet voices selected');
    await shot(ad, '8-admin-settings');
    await ad.click('#s-test');
    await ad.waitForSelector('.res');
    check((await ad.$$('.res')).length >= 3, 'engine test reports results');
    await ad.click('[data-tab=people]');
    await ad.waitForSelector('#p-list .item');
    check((await ad.textContent('#p-list')).includes('Maria'), 'people list');

    check(errors.length === 0, 'no script errors on any page' + (errors.length ? ':\n  ' + errors.join('\n  ') : ''));
    console.log('\nall browser checks passed');
  } catch (e) {
    if (!failed) console.log('FAIL ' + e.message);
    if (errors.length) console.log('page errors:\n  ' + errors.join('\n  '));
    console.log('--- server log ---\n' + log.slice(-2000));
    process.exitCode = 1;
  } finally {
    await browser.close();
    srv.kill();
    fs.rmSync(data, {recursive: true, force: true});
  }
})();
