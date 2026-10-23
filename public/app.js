/* CTRANSLATE shared client code: API calls, sign-in, the meeting editor and
   share sheet, the audio player, the live stream, screen wake lock, the
   new-build banner and the home-screen install hint. Loaded by every page
   before its own script. Everything hangs off window.VM. */
(function () {
  'use strict';
  const VM = window.VM = {};
  VM.BUILD = '__BUILD__';
  const $ = VM.$ = id => document.getElementById(id);
  const esc = VM.esc = s => String(s == null ? '' : s).replace(/[&<>"']/g,
    c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
  const store = VM.store = {
    get(k, d) { try { const v = localStorage.getItem('vm.' + k); return v == null ? d : JSON.parse(v); } catch (e) { return d; } },
    set(k, v) { try { localStorage.setItem('vm.' + k, JSON.stringify(v)); } catch (e) {} }
  };

  /* ---------- API ---------- */
  VM.api = async function (method, path, body, opts) {
    const o = {method, headers: {'X-VM': '1'}, credentials: 'same-origin'};
    if (body instanceof ArrayBuffer || body instanceof Blob) {
      o.body = body; o.headers['Content-Type'] = 'application/octet-stream';
    } else if (body !== undefined) {
      o.body = JSON.stringify(body); o.headers['Content-Type'] = 'application/json';
    }
    if (opts && opts.signal) o.signal = opts.signal;
    let r;
    try { r = await fetch('/api/' + path, o); }
    catch (e) { const err = new Error('Can’t reach the CTRANSLATE server. Check the network.'); err.status = 0; throw err; }
    let d = {};
    try { d = await r.json(); } catch (e) {}
    if (!r.ok) { const err = new Error(d.error || ('Error ' + r.status)); err.status = r.status; throw err; }
    return d;
  };

  VM.config = null;
  VM.me = null;
  VM.load = async function () {
    VM.config = await VM.api('GET', 'config');
    try { VM.me = (await VM.api('GET', 'me')).me; } catch (e) { if (e.status !== 401) throw e; VM.me = null; }
    return VM.me;
  };
  VM.lang = code => (VM.config.allLanguages.find(l => l.id === code) || {id: code, label: code, tag: code.toUpperCase(), color: '#78aaff', sr: code, tts: ''});
  VM.fillLangs = function (sel, value, codes) {
    const list = (codes || VM.config.languages.map(l => l.id));
    if (value && !list.includes(value)) list.push(value);
    sel.innerHTML = list.map(c => { const l = VM.lang(c); return '<option value="' + esc(c) + '">' + esc(l.label + ' · ' + l.tag) + '</option>'; }).join('');
    if (value) sel.value = value;
  };

  /* ---------- small UI ---------- */
  let toastTimer = null;
  VM.toast = function (msg, ms) {
    let t = $('vm-toast');
    if (!t) { t = document.createElement('div'); t.id = 'vm-toast'; t.className = 'toast'; t.setAttribute('role', 'status'); document.body.appendChild(t); }
    t.textContent = msg; t.classList.add('show');
    clearTimeout(toastTimer); toastTimer = setTimeout(() => t.classList.remove('show'), ms || 3200);
  };
  VM.modal = function (html, onClose) {
    const m = document.createElement('div');
    m.className = 'modal';
    m.innerHTML = '<div class="box" role="dialog" aria-modal="true">' + html + '</div>';
    const close = () => { m.remove(); document.removeEventListener('keydown', key); if (onClose) onClose(); };
    const key = e => { if (e.key === 'Escape') close(); };
    m.addEventListener('click', e => { if (e.target === m) close(); });
    document.addEventListener('keydown', key);
    document.body.appendChild(m);
    m.querySelectorAll('[data-close]').forEach(b => b.onclick = close);
    return {el: m, box: m.firstChild, close, q: s => m.querySelector(s)};
  };
  VM.confirm = function (msg, yes, danger) {
    return new Promise(res => {
      const d = VM.modal('<h3>' + esc(msg) + '</h3><div class="actions"><button class="btn big" data-close>CANCEL</button>' +
        '<button class="btn big ' + (danger ? 'stop' : 'go') + '" id="vm-yes">' + esc(yes || 'OK') + '</button></div>', () => res(false));
      d.q('#vm-yes').onclick = () => { res(true); d.el.remove(); };
    });
  };
  VM.fmtWhen = function (ms) {
    if (!ms) return 'No time set';
    const d = new Date(ms), now = new Date();
    const day = d.toDateString() === now.toDateString() ? 'Today'
      : d.toDateString() === new Date(now.getTime() + 864e5).toDateString() ? 'Tomorrow'
      : d.toLocaleDateString(undefined, {weekday: 'short', month: 'short', day: 'numeric'});
    return day + ' ' + d.toLocaleTimeString(undefined, {hour: 'numeric', minute: '2-digit'});
  };
  VM.statusLabel = m => m.status === 'live' ? 'LIVE' : m.status === 'ended' ? 'ENDED' : 'SCHEDULED';

  /* ---------- header ---------- */
  VM.header = function (app) {
    const b = $('buildStamp');
    if (b) b.textContent = (app ? app + ' · ' : '') + 'BUILD ' + VM.BUILD;
    const who = $('who');
    if (who) who.textContent = VM.me && VM.me.kind === 'user' ? (VM.me.name + ' · ' + VM.me.role.toUpperCase()) : '';
    const out = $('signout');
    if (out) {
      out.hidden = !VM.me || VM.me.kind === 'guest';
      out.onclick = async () => { try { await VM.api('POST', 'logout'); } catch (e) {} location.reload(); };
    }
  };

  /* ---------- sign in ---------- */
  VM.signIn = function (box, opts) {
    opts = opts || {};
    box.innerHTML =
      '<div class="card"><h2>' + esc(opts.title || 'Sign in') + '</h2>' +
      (opts.note ? '<p class="hint">' + esc(opts.note) + '</p>' : '') +
      '<form id="vm-signin" autocomplete="on">' +
      '<label class="f" for="vm-name">Your name</label><input id="vm-name" name="username" autocomplete="username" autocapitalize="words" required>' +
      '<label class="f" for="vm-pin">PIN</label><input id="vm-pin" name="password" type="password" inputmode="numeric" autocomplete="current-password" required>' +
      '<div class="err-text" id="vm-signin-err"></div>' +
      '<button class="btn big go wide" type="submit">SIGN IN</button></form></div>';
    const last = store.get('lastName', '');
    if (last) { $('vm-name').value = last; }
    $('vm-signin').onsubmit = async e => {
      e.preventDefault();
      $('vm-signin-err').textContent = '';
      try {
        const r = await VM.api('POST', 'login', {name: $('vm-name').value, pin: $('vm-pin').value});
        store.set('lastName', $('vm-name').value.trim());
        VM.me = r.me;
        if (opts.done) opts.done(r.me);
      } catch (err) { $('vm-signin-err').textContent = err.message; $('vm-pin').value = ''; }
    };
    setTimeout(() => (last ? $('vm-pin') : $('vm-name')).focus(), 50);
  };

  /* ---------- meeting editor (speakers and admins) ---------- */
  function toLocalInput(ms) {
    const d = new Date(ms), p = n => String(n).padStart(2, '0');
    return d.getFullYear() + '-' + p(d.getMonth() + 1) + '-' + p(d.getDate()) + 'T' + p(d.getHours()) + ':' + p(d.getMinutes());
  }
  VM.editMeeting = async function (m, onSaved) {
    let people = [];
    try { people = (await VM.api('GET', 'people')).people; } catch (e) { VM.toast(e.message); }
    const isNew = !m;
    m = m || {title: '', start: null, duration: 60, access: 'staff', hosts: VM.me ? [{id: VM.me.id}] : [], listeners: [], notes: ''};
    const hostIds = new Set(m.hosts.map(h => h.id)), listenIds = new Set(m.listeners.map(h => h.id));
    const pick = (name, list, chosen) => list.length ? list.map(p =>
      '<label><input type="checkbox" name="' + name + '" value="' + esc(p.id) + '"' + (chosen.has(p.id) ? ' checked' : '') + '>' +
      esc(p.name) + '<span class="sub">' + esc(VM.lang(p.lang).tag) + ' · ' + esc(p.role.toUpperCase()) + '</span></label>').join('')
      : '<div class="empty">Nobody yet. An admin adds people on the People tab.</div>';
    const speakers = people.filter(p => p.role !== 'listener');
    const start = m.start || (Math.ceil(Date.now() / 9e5) * 9e5);
    const d = VM.modal(
      '<h3>' + (isNew ? 'NEW MEETING' : 'EDIT MEETING') + '</h3>' +
      '<label class="f" for="me-title">Title</label><input id="me-title" maxlength="120" value="' + esc(m.title) + '" placeholder="Monday safety briefing">' +
      '<div class="row"><div class="grow"><label class="f" for="me-start">Starts</label><input id="me-start" type="datetime-local" value="' + toLocalInput(start) + '"></div>' +
      '<div style="width:110px"><label class="f" for="me-dur">Minutes</label><input id="me-dur" type="number" min="5" max="1440" step="5" value="' + (m.duration || 60) + '"></div></div>' +
      '<label class="f" for="me-access">Who can join</label><select id="me-access">' +
      '<option value="invited">Only the people invited below</option>' +
      '<option value="staff">Anyone signed in to CTRANSLATE</option>' +
      (VM.config.guestsAllowed ? '<option value="anyone">Anyone who scans the QR code (no sign-up)</option>' : '') + '</select>' +
      '<label style="display:flex;align-items:center;margin-top:12px;font-size:13px"><input type="checkbox" id="me-standing"' + (m.standing ? ' checked' : '') + '>Standing room: the code and printed QR keep working for every session</label>' +
      '<label style="display:flex;align-items:center;margin-top:8px;font-size:13px"><input type="checkbox" id="me-onsite"' + (m.onsite ? ' checked' : '') + '>Guests only on the company Wi-Fi (not over the internet)</label>' +
      '<label class="f">Speakers</label><div class="picklist" id="me-hosts">' + pick('h', speakers, hostIds) + '</div>' +
      '<label class="f">Listeners invited</label><input id="me-filter" placeholder="Search names" style="margin-bottom:6px">' +
      '<div class="picklist" id="me-listeners">' + pick('l', people, listenIds) + '</div>' +
      '<label class="f" for="me-notes">Notes (only speakers and admins see these)</label><textarea id="me-notes" maxlength="2000">' + esc(m.notes || '') + '</textarea>' +
      '<div class="err-text" id="me-err"></div>' +
      '<div class="actions"><button class="btn big" data-close>CANCEL</button><button class="btn big go" id="me-save">' + (isNew ? 'CREATE' : 'SAVE') + '</button></div>');
    d.q('#me-access').value = m.access || 'staff';
    d.q('#me-filter').oninput = e => {
      const f = e.target.value.trim().toLowerCase();
      d.q('#me-listeners').querySelectorAll('label').forEach(l => { l.hidden = f && !l.textContent.toLowerCase().includes(f); });
    };
    d.q('#me-title').focus();
    d.q('#me-save').onclick = async () => {
      const val = s => Array.from(d.el.querySelectorAll('input[name=' + s + ']:checked')).map(i => i.value);
      const st = d.q('#me-start').value;
      const body = {title: d.q('#me-title').value, start: st ? new Date(st).getTime() : null,
        duration: +d.q('#me-dur').value || 60, access: d.q('#me-access').value,
        hosts: val('h'), listeners: val('l'), notes: d.q('#me-notes').value,
        standing: d.q('#me-standing').checked, onsite: d.q('#me-onsite').checked};
      try {
        const r = isNew ? await VM.api('POST', 'meetings', body) : await VM.api('PUT', 'meetings/' + m.id, body);
        d.close();
        if (onSaved) onSaved(r.meeting);
      } catch (e) { d.q('#me-err').textContent = e.message; }
    };
  };

  /* ---------- share: code, link, QR ---------- */
  VM.joinLink = m => m.joinUrl.startsWith('http') ? m.joinUrl : location.origin + m.joinUrl;
  VM.share = function (m, onChange) {
    const link = VM.joinLink(m);
    let qr = '';
    try { qr = window.VMQR ? VMQR.svg(link) : ''; } catch (e) {}
    const who = m.access === 'anyone' ? 'Anyone who scans this code can listen right away, with no sign-up.' +
        (m.onsite ? ' Guests must be on the company Wi-Fi.' : '')
      : m.access === 'staff' ? 'Anyone signed in to CTRANSLATE can join with this code.' : 'Only the people invited can join.';
    const d = VM.modal('<h3>' + esc(m.title) + '</h3><p class="hint">' + esc(who) + '</p>' +
      '<div style="text-align:center"><div class="hint">MEETING CODE</div><div class="code-big">' + esc(m.code) + '</div>' +
      (qr ? '<div class="qr">' + qr + '</div>' : '') +
      '<div class="hint" style="word-break:break-all">' + esc(link) + '</div></div>' +
      '<div class="actions"><a class="btn big on" href="/print?m=' + encodeURIComponent(m.id) + '" target="_blank" rel="noopener">PRINT QR SHEET</a>' +
      '<button class="btn big" id="sh-copy">COPY LINK</button>' +
      (m.canHost ? '<button class="btn big" id="sh-new" title="Every printed copy stops working">NEW CODE</button>' : '') +
      (navigator.share ? '<button class="btn big" id="sh-share">SHARE…</button>' : '') +
      '<button class="btn big go" data-close>DONE</button></div>');
    d.q('#sh-copy').onclick = async () => {
      try { await navigator.clipboard.writeText(link); VM.toast('Link copied.'); }
      catch (e) { VM.toast(link, 8000); }
    };
    const nc = d.q('#sh-new');
    if (nc) nc.onclick = async () => {
      if (!(await VM.confirm('Give this meeting a new code? Printed sheets and shared links stop working, and guests listening now are disconnected.', 'NEW CODE', true))) return;
      try {
        const r = await VM.api('POST', 'meetings/' + m.id + '/newcode');
        d.close(); if (onChange) onChange(r.meeting); VM.share(r.meeting, onChange);
      } catch (e) { VM.toast(e.message); }
    };
    const s = d.q('#sh-share');
    if (s) s.onclick = () => navigator.share({title: m.title, text: 'Join "' + m.title + '" on CTRANSLATE — code ' + m.code, url: link}).catch(() => {});
  };

  /* ---------- screen wake lock ----------
     Held while in a meeting so the phone never auto-locks. The browser drops
     the lock whenever the page is hidden; it is taken again on return. */
  VM.Wake = {
    want: false, lock: null,
    async on() {
      this.want = true;
      if (this.lock || !('wakeLock' in navigator)) return !!this.lock || 'wakeLock' in navigator;
      try {
        const l = await navigator.wakeLock.request('screen');
        if (!this.want) { l.release().catch(() => {}); return false; }
        this.lock = l;
        l.addEventListener('release', () => { if (this.lock === l) this.lock = null; });
        return true;
      } catch (e) { return false; }
    },
    off() { this.want = false; const l = this.lock; this.lock = null; if (l) l.release().catch(() => {}); }
  };
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'visible' && VM.Wake.want) VM.Wake.on();
  });

  /* ---------- audio player ----------
     One <audio> element plays every voiced phrase in phrase order. Between
     phrases it loops a second of silence, so the phone keeps the audio
     session (and the page) alive with the screen off or the app in the
     background, and a headset's controls stay attached. iOS only lets an
     element play after a tap, so unlock() runs inside the Join tap. Phrases
     without a server voice are spoken by the device's own voice when it has
     one for the language. */
  function silenceWav() {
    const n = 8000, buf = new Uint8Array(44 + n), v = new DataView(buf.buffer);
    const w = (o, s) => { for (let i = 0; i < s.length; i++) buf[o + i] = s.charCodeAt(i); };
    w(0, 'RIFF'); v.setUint32(4, 36 + n, true); w(8, 'WAVE'); w(12, 'fmt ');
    v.setUint32(16, 16, true); v.setUint16(20, 1, true); v.setUint16(22, 1, true);
    v.setUint32(24, 8000, true); v.setUint32(28, 8000, true); v.setUint16(32, 1, true); v.setUint16(34, 8, true);
    w(36, 'data'); v.setUint32(40, n, true); buf.fill(128, 44);
    let s = ''; buf.forEach(b => { s += String.fromCharCode(b); });
    return 'data:audio/wav;base64,' + btoa(s);
  }
  const SILENCE = silenceWav();

  class Player {
    constructor() {
      this.el = new Audio();
      this.el.setAttribute('playsinline', '');
      this.el.preload = 'auto';
      this.queue = [];
      this.current = null;
      this.enabled = true;
      this.suspended = false;
      this.keepAlive = true;
      this.rate = 1;
      this.lang = 'en';
      this.deviceVoice = true;     // speak phrases that have no server voice
      this.onspeak = null;         // (segId|null) while a phrase plays
      this.timer = null;
      this.el.addEventListener('ended', () => { if (this.current && this.current.kind === 'clip') this.done(); });
      this.el.addEventListener('error', () => { if (this.current && this.current.kind === 'clip') this.done(); });
    }
    unlock() {
      this.unlocked = true;
      this.idle();
      try {   // iOS: the speech engine also wants a first call inside a tap
        if (window.speechSynthesis) { const u = new SpeechSynthesisUtterance(' '); u.volume = 0; speechSynthesis.speak(u); }
      } catch (e) {}
    }
    idle() {
      if (!this.unlocked || this.current) return;
      if (this.keepAlive && this.enabled) {
        if (this.el.src !== SILENCE || this.el.paused) {
          this.el.loop = true; this.el.src = SILENCE; this.el.playbackRate = 1;
          this.el.play().catch(() => {});
        }
      } else { this.el.pause(); }
    }
    add(seg) {
      if (!this.enabled || this.suspended) return;
      this.queue.push({id: seg.id, url: seg.audio, text: seg.text, t: Date.now()});
      this.queue.sort((a, b) => a.id - b.id);
      this.pump();
    }
    audio(id, url) {
      const it = this.queue.find(q => q.id === id);
      if (it) { it.url = url || null; this.pump(); }
    }
    pump() {
      clearTimeout(this.timer);
      if (this.current || !this.enabled || this.suspended) return;
      // Falling behind (a fast talker, a slow voice): drop what is too old to be useful.
      const now = Date.now();
      this.queue = this.queue.filter(q => now - q.t < 45000);
      while (this.queue.length > 6) this.queue.shift();
      const head = this.queue[0];
      if (!head) { this.idle(); if (this.onspeak) this.onspeak(null); return; }
      if (head.url === 'pending') {
        if (now - head.t < 7000) { this.timer = setTimeout(() => this.pump(), 250); this.idle(); return; }
        head.url = null;
      }
      this.queue.shift();
      const hurry = this.queue.length >= 2 ? 1.15 : 1;
      if (head.url) {
        this.current = {kind: 'clip', id: head.id};
        this.el.loop = false;
        this.el.src = head.url;
        this.el.playbackRate = Math.min(2, this.rate * hurry);
        this.el.play().catch(() => this.done());
      } else if (this.deviceVoice && head.text && window.speechSynthesis) {
        this.current = {kind: 'tts', id: head.id};
        const u = new SpeechSynthesisUtterance(head.text), l = VM.lang(this.lang);
        u.lang = l.tts || l.sr;
        const vs = speechSynthesis.getVoices(), want = (l.tts || l.sr || '').toLowerCase(), base = want.split('-')[0];
        u.voice = vs.find(v => v.lang.replace('_', '-').toLowerCase() === want) ||
                  vs.find(v => v.lang.toLowerCase().startsWith(base)) || null;
        u.rate = Math.min(2, this.rate * hurry);
        let fin = false;
        const end = () => { if (!fin) { fin = true; clearTimeout(guard); this.done(); } };
        const guard = setTimeout(end, 4000 + head.text.length * 120);   // some engines never fire 'end'
        u.onend = end; u.onerror = end;
        speechSynthesis.speak(u);
      } else { this.pump(); return; }
      if (this.onspeak) this.onspeak(head.id);
    }
    done() { this.current = null; this.pump(); }
    stopAll() {
      clearTimeout(this.timer);
      this.queue = [];
      if (this.current) { this.current = null; }
      try { if (window.speechSynthesis) speechSynthesis.cancel(); } catch (e) {}
      if (this.onspeak) this.onspeak(null);
    }
    setEnabled(on) { this.enabled = on; if (!on) this.stopAll(); this.idle(); }
    suspend(on) { this.suspended = on; if (on) { this.stopAll(); this.idle(); } else this.pump(); }
    shutdown() { this.stopAll(); this.unlocked = false; this.el.pause(); this.el.removeAttribute('src'); this.el.load(); }
    async setSink(id) { if (this.el.setSinkId) { try { await this.el.setSinkId(id || ''); return true; } catch (e) {} } return false; }
  }
  VM.Player = Player;
  VM.canPickOutput = () => 'setSinkId' in HTMLMediaElement.prototype;

  /* ---------- live stream ----------
     EventSource reconnects by itself and sends Last-Event-ID, so phrases
     missed during a blip are replayed. A stream the server closed for good
     (meeting ended, not allowed) is reported once through onclosed. */
  class Stream {
    constructor(mid, params, handlers) {
      this.mid = mid; this.params = params; this.h = handlers; this.lastId = 0; this.es = null; this.stopped = false;
      this.onVis = () => { if (document.visibilityState === 'visible' && !this.stopped && (!this.es || this.es.readyState === 2)) this.open(); };
      document.addEventListener('visibilitychange', this.onVis);
      addEventListener('online', this.onVis);
    }
    open() {
      if (this.es) this.es.close();
      const p = new URLSearchParams(this.params);
      if (this.lastId) p.set('since', this.lastId);
      const es = this.es = new EventSource('/api/meetings/' + this.mid + '/events?' + p.toString());
      ['hello', 'seg', 'audio', 'live', 'roster', 'status', 'kicked'].forEach(name => es.addEventListener(name, e => {
        if (es !== this.es) return;
        let d = {}; try { d = JSON.parse(e.data); } catch (x) { return; }
        if (name === 'seg' && d.id > this.lastId) this.lastId = d.id;
        if (name === 'hello') (d.history || []).forEach(s => { if (s.id > this.lastId) this.lastId = s.id; });
        if (this.h[name]) this.h[name](d);
      }));
      es.onopen = () => { if (this.h.open) this.h.open(); };
      es.onerror = () => {
        if (es !== this.es || this.stopped) return;
        if (es.readyState === 2) { if (this.h.closed) this.h.closed(); }
        else if (this.h.reconnecting) this.h.reconnecting();
      };
    }
    close() {
      this.stopped = true;
      document.removeEventListener('visibilitychange', this.onVis);
      removeEventListener('online', this.onVis);
      if (this.es) this.es.close();
      this.es = null;
    }
  }
  VM.Stream = Stream;

  VM.mediaSession = function (title, sub, onToggle) {
    if (!('mediaSession' in navigator)) return;
    try {
      navigator.mediaSession.metadata = new MediaMetadata({title, artist: sub, album: 'CTRANSLATE',
        artwork: [{src: '/apps/listen-512.png', sizes: '512x512', type: 'image/png'}]});
      ['play', 'pause'].forEach(a => navigator.mediaSession.setActionHandler(a, () => onToggle(a === 'play')));
    } catch (e) {}
  };

  /* ---------- new-build banner ---------- */
  const newer = (a, b) => { if (!a || !b) return false; const [da, na] = a.split('.'), [db, nb] = b.split('.');
    return da > db || (da === db && (+na || 0) > (+nb || 0)); };
  VM.watchBuild = function () {
    let bar = $('update');
    if (!bar) {
      bar = document.createElement('div'); bar.className = 'update'; bar.id = 'update';
      bar.innerHTML = '<span id="updateT"></span><button class="btn" id="updateGo">RELOAD</button><button class="x" id="updateX" title="Dismiss">&#10005;</button>';
      document.body.prepend(bar);
      $('updateGo').onclick = () => { location.href = location.pathname + '?v=' + Date.now() + (location.search ? '&' + location.search.slice(1) : ''); };
      $('updateX').onclick = () => bar.classList.remove('show');
    }
    setInterval(() => {
      fetch('/api/config?_cb=' + Date.now(), {cache: 'no-store'}).then(r => r.ok ? r.json() : null).then(c => {
        if (c && newer(c.build, VM.BUILD)) {
          $('updateT').textContent = 'A NEW BUILD OF CTRANSLATE IS AVAILABLE · ' + c.build + ' (YOU ARE ON ' + VM.BUILD + ')';
          bar.classList.add('show');
        }
      }).catch(() => {});
    }, 60000);
  };

  /* ---------- pinnable app: service worker + install hint ---------- */
  VM.standalone = () => !!(navigator.standalone || (matchMedia && matchMedia('(display-mode: standalone)').matches));
  VM.registerApp = function (app) {
    if ('serviceWorker' in navigator && location.protocol === 'https:') {
      navigator.serviceWorker.register('/sw.js', {scope: '/' + app}).catch(() => {});
    }
    let deferred = null;
    addEventListener('beforeinstallprompt', e => { e.preventDefault(); deferred = e; show(); });
    function show() {
      if (VM.standalone() || store.get('noInstall.' + app)) return;
      const ua = navigator.userAgent, ios = /iPhone|iPad|iPod/.test(ua) || (/Macintosh/.test(ua) && navigator.maxTouchPoints > 1);
      if (!ios && !deferred) return;
      let bar = $('vm-install');
      if (bar) return;
      bar = document.createElement('div');
      bar.id = 'vm-install';
      bar.className = 'bar';
      bar.style.cssText = 'position:fixed;left:0;right:0;bottom:0;z-index:45;justify-content:space-between';
      bar.innerHTML = '<span class="hint" style="flex:1">' + (ios
        ? 'Pin this app: tap <b>Share</b> ↑ then <b>Add to Home Screen</b>.'
        : 'Pin this app to your home screen or desktop.') + '</span>' +
        (deferred ? '<button class="btn go" id="vm-install-go">INSTALL</button>' : '') +
        '<button class="btn ghost" id="vm-install-x">NOT NOW</button>';
      document.body.appendChild(bar);
      const go = $('vm-install-go');
      if (go) go.onclick = async () => { deferred.prompt(); try { await deferred.userChoice; } catch (e) {} bar.remove(); };
      $('vm-install-x').onclick = () => { store.set('noInstall.' + app, true); bar.remove(); };
    }
    setTimeout(show, 1500);
  };
})();
