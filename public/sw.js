/* ============================================================================
   Cloud HDR — service worker.
   Нужен для двух вещей: окно приложения открывается мгновенно (оболочка берётся
   из кеша, даже если агент ещё поднимается) и Chrome предлагает «Установить».

   Стратегия: network-first для оболочки (правки в CSS/JS видны сразу),
   кеш — только как запасной вариант. Запросы к /api/ не кешируются никогда.
   ========================================================================== */

/*
   Версия в имени кеша обязана меняться вместе с оболочкой. Обработчик
   activate удаляет все кеши, кроме текущего, — так старые файлы уходят
   гарантированно, а не «когда-нибудь по сроку годности». Стратегия
   network-first и без этого показала бы свежие файлы, но запасной вариант
   должен быть свежим тоже: иначе при неподнятом агенте окно откроется в
   прошлогоднем виде.
*/
const CACHE = 'cloudhdr-shell-v3';

// Полный список скриптов, а не три из девяти, как было. Неполный список делал
// запасной вариант нерабочим: оболочка бралась из кеша, а половина модулей —
// из сети, которой в этот момент и нет.
const SHELL = [
  '/',
  '/index.html',
  '/css/style.css',
  '/js/brain.js',
  '/js/skills.js',
  '/js/tokens.js',
  '/js/llm.js',
  '/js/nlu-spacy.js',
  '/js/nlu.js',
  '/js/sos.js',
  '/js/tts.js',
  '/js/voice.js',
  '/js/api.js',
  '/js/cursor.js',
  '/js/vision.js',
  '/js/app.js',
  '/manifest.webmanifest',
  '/icons/icon-192.png',
  '/icons/icon-512.png'
];

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE)
      .then((cache) => cache.addAll(SHELL))
      .then(() => self.skipWaiting())
      .catch(() => self.skipWaiting())
  );
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((key) => key !== CACHE).map((key) => caches.delete(key))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', (event) => {
  const url = new URL(event.request.url);

  // Команды агенту должны идти строго в сеть — кешировать их нельзя.
  if (url.pathname.startsWith('/api/')) return;
  if (event.request.method !== 'GET') return;
  if (url.origin !== location.origin) return;

  event.respondWith(
    fetch(event.request)
      .then((response) => {
        const copy = response.clone();
        caches.open(CACHE).then((cache) => cache.put(event.request, copy)).catch(() => {});
        return response;
      })
      .catch(() => caches.match(event.request).then((cached) => cached || caches.match('/index.html')))
  );
});

// Позволяет странице попросить обновиться после установки новой версии.
self.addEventListener('message', (event) => {
  if (event.data === 'skipWaiting') self.skipWaiting();
});
