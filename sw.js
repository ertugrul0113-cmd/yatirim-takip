// Yatırım Takip: internet yokken son görülen veriyi göstermek için önbellek.
// Sayfa ve veriler her zaman önce internetten istenir; gelmezse önbellektekini kullanır.
const CACHE = "yt-v1";
const LIB = "https://cdn.jsdelivr.net/npm/lightweight-charts@4.1.3/dist/lightweight-charts.standalone.production.js";
const SHELL = ["./", "manifest.webmanifest", "icons/icon-192.png", "icons/apple-touch-icon.png"];
const NET_HOSTS = ["data-api.binance.vision", "api.alternative.me"];

self.addEventListener("install", e => {
  e.waitUntil(caches.open(CACHE).then(c => Promise.allSettled([...SHELL, LIB].map(u => c.add(u)))).then(() => self.skipWaiting()));
});

self.addEventListener("activate", e => {
  e.waitUntil(caches.keys().then(ks => Promise.all(ks.filter(k => k !== CACHE).map(k => caches.delete(k)))).then(() => self.clients.claim()));
});

self.addEventListener("fetch", e => {
  const req = e.request;
  if (req.method !== "GET") return;
  const url = new URL(req.url);
  const same = url.origin === self.location.origin;
  if (req.url === LIB){ // değişmeyen kütüphane: önce önbellek
    e.respondWith(caches.match(req).then(r => r || fetch(req).then(res => { const c = res.clone(); caches.open(CACHE).then(x => x.put(req, c)); return res; })));
    return;
  }
  if (!same && !NET_HOSTS.includes(url.hostname)) return;
  // aynı sitedeki dosyalar ?v=... olmadan saklanır
  const key = same ? (req.mode === "navigate" ? new URL("./", self.location).href : url.origin + url.pathname) : req.url;
  e.respondWith(
    fetch(req).then(res => {
      if (res.ok){ const c = res.clone(); caches.open(CACHE).then(x => x.put(key, c)); }
      return res;
    }).catch(() => caches.match(key).then(r => r || Response.error()))
  );
});

self.addEventListener("notificationclick", e => {
  e.notification.close();
  e.waitUntil(self.clients.matchAll({type: "window", includeUncontrolled: true}).then(cs => {
    for (const c of cs) if ("focus" in c) return c.focus();
    return self.clients.openWindow("./");
  }));
});
