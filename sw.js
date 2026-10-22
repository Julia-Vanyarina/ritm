// Ритм — офлайн-режим. При обновлении приложения поменяйте номер версии (и APP_VERSION в index.html).
const VERSION = 'ritm-v4';
const SHELL = ['./', 'index.html', 'manifest.webmanifest', 'icons/icon-180.png', 'icons/icon-192.png', 'icons/icon-512.png'];

self.addEventListener('install', e => {
  // cache: 'reload' — берём файлы прямо с сервера, а не из кэша браузера
  e.waitUntil(caches.open(VERSION)
    .then(c => c.addAll(SHELL.map(u => new Request(u, { cache: 'reload' }))))
    .then(() => self.skipWaiting()));
});
self.addEventListener('activate', e => {
  e.waitUntil(caches.keys().then(ks => Promise.all(ks.filter(k => k !== VERSION).map(k => caches.delete(k)))).then(() => self.clients.claim()));
});
self.addEventListener('fetch', e => {
  const url = new URL(e.request.url);
  if (e.request.method !== 'GET') return;
  // база продуктов и прочие внешние сервисы — только через сеть
  if (url.hostname.includes('openfoodfacts') || url.hostname.includes('yandex')) return;
  // страница приложения: всегда свежая с сервера, без сети — из кэша
  if (e.request.mode === 'navigate' || url.pathname.endsWith('/index.html')) {
    e.respondWith(fetch(e.request.url, { cache: 'no-store' })
      .then(r => { if (r.ok) { const copy = r.clone(); caches.open(VERSION).then(c => c.put('index.html', copy)); } return r; })
      .catch(() => caches.match('index.html')));
    return;
  }
  // остальное (значки, сканер штрихкодов): из кэша, иначе из сети с сохранением
  e.respondWith(caches.match(e.request).then(hit => hit || fetch(e.request).then(r => {
    if (r.ok || r.type === 'opaque') { const copy = r.clone(); caches.open(VERSION).then(c => c.put(e.request, copy)); }
    return r;
  })));
});
