// TrackTab Service Worker
//
// Zweck ausschliesslich Installierbarkeit (Chrome/Edge-Installiersymbol,
// Safaris "Zum Dock hinzufuegen") plus eine freundliche Meldung, wenn der
// lokale Server nicht erreichbar ist -- KEIN echtes Offline-Caching von
// App-Daten. TrackTab haengt fuer alles (Datenbank, ffmpeg/ffprobe,
// AppleScript-Automation, Audio-Streaming) am laufenden lokalen Server;
// eine gecachte Kopie davon waere immer veraltet. Deshalb kein
// caches.match()/caches.put() irgendwo in dieser Datei.

self.addEventListener("install", () => {
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(self.clients.claim());
});

// Nur fuer Seitenaufrufe (mode "navigate") -- /api/*, Audio-Streaming,
// Waveform und Cover werden unveraendert durchgereicht.
const OFFLINE_HTML = `<!DOCTYPE html>
<html lang="de"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>TrackTab</title>
<style>
  body { font: 15px -apple-system, BlinkMacSystemFont, sans-serif; background: #24293a;
         color: #e6e9f2; display: flex; align-items: center; justify-content: center;
         height: 100vh; margin: 0; }
  div { text-align: center; max-width: 26em; padding: 0 1.5em; }
  p:last-child { opacity: .7; font-size: .9em; }
</style></head>
<body><div>
  <p><strong>Server nicht erreichbar.</strong><br>Bitte TrackTab starten.</p>
  <p>Server not reachable. Please start TrackTab.</p>
</div></body></html>`;

self.addEventListener("fetch", (event) => {
  if (event.request.mode !== "navigate") return;
  event.respondWith(
    fetch(event.request).catch(() => new Response(OFFLINE_HTML, {
      headers: { "Content-Type": "text/html; charset=utf-8" },
    }))
  );
});
