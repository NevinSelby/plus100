/* Plus100 service worker: cache the app shell, never cache API responses
   (odds and predictions must always be fresh). */
const CACHE = "plus100-v24";
const SHELL = ["/", "/static/style.css?v=24", "/static/app.js?v=24",
               "/static/manifest.json", "/static/icon-192.png"];

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (e) => {
  e.waitUntil(caches.keys().then((keys) =>
    Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k)))
  ).then(() => self.clients.claim()));
});

self.addEventListener("fetch", (e) => {
  const url = new URL(e.request.url);
  if (url.pathname.startsWith("/api/")) return;           // always live
  e.respondWith(
    fetch(e.request).then((res) => {
      if (res.ok && e.request.method === "GET" && url.origin === location.origin) {
        const clone = res.clone();
        caches.open(CACHE).then((c) => c.put(e.request, clone));
      }
      return res;
    }).catch(() => caches.match(e.request))
  );
});
