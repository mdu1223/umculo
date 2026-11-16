// Umculo offline: the app itself, and the songs you saved to this phone.
// Saved songs live in the "umc-offline" cache, keyed by their address without the query (?can=… &k=…),
// and play from there with seeking (Range requests), with or without a connection.
const SHELL = "umc-shell-v1", OFF = "umc-offline", COVERS = "umc-covers";
const SHELL_FILES = ["/", "/manifest.json", "/icon-192.png", "/icon-512.png", "/apple-touch-icon.png"];

self.addEventListener("install", e => { e.waitUntil(caches.open(SHELL).then(c => c.addAll(SHELL_FILES)).catch(() => {})); self.skipWaiting(); });
self.addEventListener("activate", e => { e.waitUntil(caches.keys().then(ks => Promise.all(ks.filter(k => k.startsWith("umc-shell") && k !== SHELL).map(k => caches.delete(k))))); self.clients.claim(); });

const songPath = p => /^\/api\/(ndstream|stream)\/[\w-]+$/.test(p);

async function ranged(req, res) {
  const range = req.headers.get("Range");
  const type = res.headers.get("Content-Type") || "audio/mpeg";
  const buf = await res.arrayBuffer(), size = buf.byteLength;
  if (!range) return new Response(buf, {status: 200, headers: {"Content-Type": type, "Content-Length": String(size), "Accept-Ranges": "bytes"}});
  const m = /bytes=(\d*)-(\d*)/.exec(range) || [];
  let start = m[1] ? +m[1] : size - +(m[2] || 0), end = m[1] && m[2] ? +m[2] : size - 1;
  start = Math.max(0, Math.min(start, size - 1)); end = Math.min(end, size - 1);
  return new Response(buf.slice(start, end + 1), {status: 206, headers: {"Content-Type": type, "Content-Length": String(end - start + 1),
    "Content-Range": `bytes ${start}-${end}/${size}`, "Accept-Ranges": "bytes"}});
}

self.addEventListener("fetch", e => {
  const req = e.request; if (req.method !== "GET") return;
  const url = new URL(req.url); if (url.origin !== location.origin) return;
  if (songPath(url.pathname)) {
    e.respondWith((async () => {
      const hit = await (await caches.open(OFF)).match(url.pathname);
      return hit ? ranged(req, hit) : fetch(req);
    })());
    return;
  }
  if (url.pathname.startsWith("/api/ndcover/")) {
    e.respondWith((async () => {
      const c = await caches.open(COVERS), key = url.pathname + "?s=" + (url.searchParams.get("s") || "");
      const hit = await c.match(key); if (hit) return hit;
      try { return await fetch(req); } catch (err) { return (await c.match(url.pathname + "?s=300")) || (await c.match(url.pathname + "?s=600")) || Response.error(); }
    })());
    return;
  }
  if (req.mode === "navigate") {
    e.respondWith(fetch(req).then(r => { if (r.ok) { const copy = r.clone(); caches.open(SHELL).then(c => c.put("/", copy)); } return r; })
      .catch(async () => (await caches.match("/")) || Response.error()));
  }
});
