// Plain-HTTP preview server for the Aegis add-in UI in a normal browser
// (no Office, no HTTPS cert). Serves public/ and proxies /api -> backend, so
// you can iterate on the look/flows without sideloading into Word. For the
// real Word add-in use server.mjs (HTTPS) instead.
//
//   node preview-server.mjs        # http://localhost:3030, /api -> the Aegis VM
//   AEGIS_BACKEND=http://localhost:8000 node preview-server.mjs   # local backend

import http from "node:http";
import { readFile } from "node:fs/promises";
import { extname, join, normalize, sep } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = fileURLToPath(new URL(".", import.meta.url));
const PUBLIC_DIR = join(ROOT, "public");
const PORT = Number(process.env.PORT || 3030);
const BACKEND = process.env.AEGIS_BACKEND || "http://10.1.128.137:8000";

const MIME = {
  ".html": "text/html; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
  ".css": "text/css; charset=utf-8",
  ".json": "application/json; charset=utf-8",
  ".png": "image/png",
  ".svg": "image/svg+xml",
  ".ico": "image/x-icon",
};

function proxy(req, res) {
  const target = new URL(req.url, BACKEND);
  const up = http.request(
    target,
    { method: req.method, headers: { ...req.headers, host: target.host } },
    (r) => { res.writeHead(r.statusCode || 502, r.headers); r.pipe(res); }
  );
  up.on("error", (e) => {
    res.writeHead(502, { "content-type": "application/json" });
    res.end(JSON.stringify({ error: "backend_unreachable", detail: String(e && e.message ? e.message : e) }));
  });
  req.pipe(up);
}

async function serveStatic(req, res) {
  let p = decodeURIComponent(new URL(req.url, "http://localhost").pathname);
  if (p === "/") p = "/taskpane.html";
  const fp = normalize(join(PUBLIC_DIR, p));
  if (fp !== PUBLIC_DIR && !fp.startsWith(PUBLIC_DIR + sep)) { res.writeHead(403); return res.end("Forbidden"); }
  try {
    const data = await readFile(fp);
    res.writeHead(200, { "content-type": MIME[extname(fp)] || "application/octet-stream" });
    res.end(data);
  } catch { res.writeHead(404); res.end("Not found"); }
}

http
  .createServer((req, res) => {
    if (req.url && req.url.startsWith("/api/")) return proxy(req, res);
    return serveStatic(req, res);
  })
  .listen(PORT, () => console.log(`Aegis add-in preview: http://localhost:${PORT}  (/api -> ${BACKEND})`));
