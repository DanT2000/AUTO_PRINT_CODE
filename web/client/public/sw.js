// Офлайн-заглушка: если помощник выключен, окно приложения открывается из кэша
// и показывает, как его запустить (вместо ошибки браузера «сайт недоступен»).
// Сначала всегда сеть — при запущенном помощнике страница всегда свежая. Кэш — только «по возможности»:
// его ошибки не должны ломать ответ.
const CACHE = "autoprintcode-v1";

self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (e) => e.waitUntil(self.clients.claim()));

self.addEventListener("fetch", (e) => {
  const req = e.request;
  const url = new URL(req.url);
  if (req.method !== "GET" || url.origin !== location.origin || url.pathname.startsWith("/api/")) return;
  e.respondWith(
    fetch(req)
      .then((res) => {
        if (res.ok) {
          const copy = res.clone();
          caches.open(CACHE).then((c) => c.put(req, copy)).catch(() => {});
        }
        return res;
      })
      .catch(async () => {
        const hit = await caches.match(req).catch(() => undefined);
        if (hit) return hit;
        if (req.mode === "navigate") {
          const root = await caches.match("/").catch(() => undefined);
          if (root) return root;
        }
        return Response.error();
      }),
  );
});
