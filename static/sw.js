self.addEventListener('install', (event) => {
    console.log('[Service Worker] Installed');
});

self.addEventListener('fetch', (event) => {
    // Basic pass-through fetch for PWA validity
    event.respondWith(fetch(event.request));
});
