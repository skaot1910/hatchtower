/* Hatchtower service worker – only so Chrome on Android offers "Install app" (it wants a worker that really
   handles page loads). Nothing is cached: every request goes to the network as before; only when a page cannot be
   loaded at all does the installed app show a short note instead of Chrome's error page. */
"use strict";

const OFFLINE = "<!doctype html><meta charset=utf-8><meta name=viewport content=\"width=device-width\">" +
  "<title>Hatchtower</title><p>Hatchtower ist gerade nicht erreichbar. / Hatchtower cannot be reached right now.</p>";

self.addEventListener("install", function () { self.skipWaiting(); });
self.addEventListener("activate", function (event) { event.waitUntil(self.clients.claim()); });

self.addEventListener("fetch", function (event) {
  if (event.request.mode !== "navigate") return;   // everything else: browser default
  event.respondWith(fetch(event.request).catch(function () {
    return new Response(OFFLINE, { status: 503, headers: { "Content-Type": "text/html; charset=utf-8" } });
  }));
});
