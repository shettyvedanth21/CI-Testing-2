import http from "http";
import next from "next";

const dev = false;
const hostname = "0.0.0.0";
const port = Number.parseInt(process.env.PORT || "3000", 10);

function isDocumentRequest(req) {
  const path = String(req.url || "").split("?")[0];
  const method = String(req.method || "GET").toUpperCase();

  if (!["GET", "HEAD"].includes(method)) {
    return false;
  }

  if (
    path.startsWith("/_next/static") ||
    path.startsWith("/_next/image") ||
    path.startsWith("/backend/") ||
    path.startsWith("/api/") ||
    path === "/favicon.ico"
  ) {
    return false;
  }

  // Treat extension-less app-shell routes as HTML/document responses even when
  // intermediaries use generic Accept headers (for example curl's */*).
  return !/\.[a-zA-Z0-9]+$/.test(path);
}

function forceDocumentNoStore(res, headers) {
  const target = headers && typeof headers === "object" ? headers : undefined;

  if (target) {
    target["Cache-Control"] = "no-store";
    target["Pragma"] = "no-cache";
    target["Expires"] = "0";
  }

  res.setHeader("Cache-Control", "no-store");
  res.setHeader("Pragma", "no-cache");
  res.setHeader("Expires", "0");
}

const app = next({ dev, hostname, port });
const handle = app.getRequestHandler();

app.prepare().then(() => {
  const server = http.createServer((req, res) => {
    if (isDocumentRequest(req)) {
      const originalWriteHead = res.writeHead.bind(res);

      res.writeHead = (...args) => {
        const [statusCode, statusMessageOrHeaders, maybeHeaders] = args;

        if (
          typeof statusMessageOrHeaders === "object" &&
          statusMessageOrHeaders !== null &&
          !Array.isArray(statusMessageOrHeaders)
        ) {
          forceDocumentNoStore(res, statusMessageOrHeaders);
          return originalWriteHead(statusCode, statusMessageOrHeaders);
        }

        if (
          typeof maybeHeaders === "object" &&
          maybeHeaders !== null &&
          !Array.isArray(maybeHeaders)
        ) {
          forceDocumentNoStore(res, maybeHeaders);
          return originalWriteHead(statusCode, statusMessageOrHeaders, maybeHeaders);
        }

        forceDocumentNoStore(res);
        return originalWriteHead(...args);
      };
    }

    return handle(req, res);
  });

  server.listen(port, hostname, () => {
    console.log(`> Ready on http://${hostname}:${port}`);
  });
});
