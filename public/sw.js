/* Service worker for the pinned apps: Listen (/listen), Speak (/speak) and
   Admin (/admin). One script, registered by each page with its own path as
   the scope, so each installed app keeps to its own pages.

   Pages go to the network first: a stale copy of an app that outlives an
   update is worse than none. The app's page, its shared scripts, manifest and
   icons are kept only as a fallback for a start with no server. /api/ is
   never touched, so nothing a sign-in opens is ever stored.

   The server writes the build into BUILD; a new build is a new script, which
   the browser installs, deleting this app's copies from the build before. */
const BUILD = '__BUILD__';
const APPS = ['listen', 'speak', 'admin'];
const SCOPE = new URL(self.registration.scope).pathname.replace(/\/+$/, '');
const APP = APPS.includes(SCOPE.slice(1)) ? SCOPE.slice(1) : '';
const PREFIX = 'ctranslate-' + APP + '-';
const CACHE = PREFIX + BUILD;
const SHARED = ['/app.css', '/app.js', '/i18n.js', '/qr.js', '/brand.png'];
const SHELL = APP ? [SCOPE, '/apps/' + APP + '.webmanifest', '/apps/' + APP + '-192.png', '/apps/' + APP + '-512.png'].concat(SHARED) : [];

self.addEventListener('install', e => {
  e.waitUntil((async () => {
    if (APP) {
      const c = await caches.open(CACHE);
      await Promise.all(SHELL.map(u => fetch(u, {cache: 'no-cache'})
        .then(r => { if (r.ok) return c.put(u, r); }).catch(() => {})));
    }
    await self.skipWaiting();
  })());
});

self.addEventListener('activate', e => {
  e.waitUntil((async () => {
    const keys = await caches.keys();
    await Promise.all(keys.filter(k => APP && k.startsWith(PREFIX) && k !== CACHE).map(k => caches.delete(k)));
    if (!APP) { await self.registration.unregister(); return; }
    await self.clients.claim();
  })());
});

function offline() {
  return new Response('<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">' +
    '<title>CTRANSLATE</title><body style="font-family:system-ui,sans-serif;background:#0d0f13;color:#e7ecf5;padding:24px">' +
    '<h1 style="font-size:18px">CTRANSLATE is not reachable</h1><p>Check the Wi-Fi or mobile data, then try again.</p>' +
    '<button onclick="location.reload()" style="font-size:16px;padding:12px 20px">↻</button>',
    {status: 503, headers: {'Content-Type': 'text/html; charset=utf-8'}});
}

self.addEventListener('fetch', e => {
  if (!APP) return;
  const req = e.request;
  if (req.method !== 'GET') return;
  const url = new URL(req.url);
  if (url.origin !== self.location.origin || url.pathname.startsWith('/api/')) return;
  const page = req.mode === 'navigate';
  if (!page && !SHELL.includes(url.pathname)) return;
  const key = page ? SCOPE : url.pathname;     // every page of this app is kept under the app's path
  e.respondWith(fetch(req).then(res => {
    if (res.ok && res.type === 'basic' && !res.redirected) {
      const copy = res.clone();
      e.waitUntil(caches.open(CACHE).then(c => c.put(key, copy)).catch(() => {}));
    }
    return res;
  }).catch(() => caches.open(CACHE).then(c => c.match(key)).then(r => r || (page ? offline() : Response.error()))));
});
