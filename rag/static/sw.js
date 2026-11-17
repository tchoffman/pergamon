/* Pergamon service worker, served from /sw.js so it covers the whole site.
 *
 * - Static files (styles, scripts, icons) come from a cache that is replaced
 *   whenever they change on the hub (the version below changes with them).
 * - Pages always come from the hub. When the hub can't be reached (the phone
 *   left the hub's Wi-Fi), the person sees a short "can't reach the hub" page
 *   instead of the browser's error.
 * - Answers, posts and other API calls are never cached or intercepted.
 */
const VERSION = "__VERSION__";
const CACHE = "pergamon-" + VERSION;
const PRECACHE = __PRECACHE__;

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(CACHE).then((c) => c.addAll(PRECACHE)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k.startsWith("pergamon-") && k !== CACHE)
        .map((k) => caches.delete(k))))
      .then(() => self.clients.claim()));
});

self.addEventListener("fetch", (event) => {
  const req = event.request;
  const url = new URL(req.url);
  if (req.method !== "GET" || url.origin !== self.location.origin) return;

  if (url.pathname.startsWith("/static/")) {
    event.respondWith(caches.match(req).then((hit) => hit || fetch(req)));
    return;
  }
  if (req.mode === "navigate") {
    event.respondWith(fetch(req).catch(() => caches.match("/offline")));
  }
});
