// Local HTTPS dev server for the Aegis Word add-in.
//
// Two jobs:
//   1. Serve the task pane (public/) over HTTPS — Office add-ins must be HTTPS,
//      even on localhost. Certs come from office-addin-dev-certs (a trusted
//      local CA), so Word's webview accepts them.
//   2. Proxy /api/* to the FastAPI backend. Because the browser only ever talks
//      to https://localhost:3001, this sidesteps both CORS and the mixed-content
//      block you'd hit calling http://localhost:8000 from an https page.
//
// Env:
//   PORT            add-in port (default 3001)
//   AEGIS_BACKEND   backend base URL (default http://localhost:8000)

import https from "node:https";
import http from "node:http";
import { readFile } from "node:fs/promises";
import { extname, join, normalize, sep } from "node:path";
import { fileURLToPath } from "node:url";
import { getHttpsServerOptions } from "office-addin-dev-certs";

const ROOT = fileURLToPath(new URL(".", import.meta.url));
const PUBLIC_DIR = join(ROOT, "public");
const PORT = Number(process.env.PORT || 3001);
const BACKEND = process.env.AEGIS_BACKEND || "http://localhost:8000";

const MIME = {
  ".html": "text/html; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
  ".css": "text/css; charset=utf-8",
  ".json": "application/json; charset=utf-8",
  ".png": "image/png",
  ".jpg": "image/jpeg",
  ".svg": "image/svg+xml",
  ".ico": "image/x-icon",
  ".map": "application/json",
};

function proxyToBackend(req, res) {
  const target = new URL(req.url, BACKEND);
  const client = target.protocol === "https:" ? https : http;
  const headers = { ...req.headers, host: target.host };
  const upstream = client.request(
    target,
    { method: req.method, headers },
    (up) => {
      res.writeHead(up.statusCode || 502, up.headers);
      up.pipe(res);
    }
  );
  upstream.on("error", (err) => {
    res.writeHead(502, { "content-type": "application/json" });
    res.end(
      JSON.stringify({
        error: "backend_unreachable",
        backend: BACKEND,
        detail: String(err && err.message ? err.message : err),
      })
    );
  });
  req.pipe(upstream);
}

async function serveStatic(req, res) {
  let pathname = decodeURIComponent(new URL(req.url, "https://localhost").pathname);
  if (pathname === "/") pathname = "/taskpane.html";
  const filePath = normalize(join(PUBLIC_DIR, pathname));
  // Block path traversal outside public/.
  if (filePath !== PUBLIC_DIR && !filePath.startsWith(PUBLIC_DIR + sep)) {
    res.writeHead(403);
    res.end("Forbidden");
    return;
  }
  try {
    const data = await readFile(filePath);
    res.writeHead(200, { "content-type": MIME[extname(filePath)] || "application/octet-stream" });
    res.end(data);
  } catch {
    res.writeHead(404, { "content-type": "text/plain" });
    res.end("Not found");
  }
}

async function main() {
  let options;
  try {
    options = await getHttpsServerOptions();
  } catch (err) {
    console.error("\nCould not load the local HTTPS dev certificate.");
    console.error("Run this once, then start again:\n  npm run certs\n");
    console.error(String(err && err.message ? err.message : err));
    process.exit(1);
  }

  const server = https.createServer(options, (req, res) => {
    if (req.url && req.url.startsWith("/api/")) return proxyToBackend(req, res);
    return serveStatic(req, res);
  });

  server.on("error", (err) => {
    // If something is already serving this port (e.g. you ran `npm run serve`
    // and then `npm run sideload`, which starts its own server), don't crash —
    // the existing server is fine. Any other error is fatal.
    if (err && err.code === "EADDRINUSE") {
      console.log(`Port ${PORT} is already serving — reusing it.`);
      process.exit(0);
    }
    console.error(String(err && err.message ? err.message : err));
    process.exit(1);
  });

  server.listen(PORT, () => {
    console.log(`\nAegis Word add-in  ->  https://localhost:${PORT}`);
    console.log(`Proxying /api      ->  ${BACKEND}`);
    console.log("Leave this running, then sideload the manifest (see README).\n");
  });
}

main();
