// HeardOS desktop service worker: makes the desktop installable as an app. Deliberately caches nothing: every page and
// API call goes to the network, so the Authentik gate always decides and nothing stale is served.
self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (e) => e.waitUntil(self.clients.claim()));
self.addEventListener("fetch", () => {});
