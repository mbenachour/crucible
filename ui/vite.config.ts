import type { ProxyOptions } from "vite";
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Several API prefixes below (/runs, /findings, /wishes) are also React
// Router paths (e.g. /runs/:runId/logs). A plain string proxy target
// forwards ANY request under that prefix to the API — including a browser's
// full-page GET on refresh/deep-link — so the API's 404 JSON is served
// instead of index.html, and the SPA route never gets a chance to render.
// Top-level navigations send `Accept: text/html` (the app's own `fetch()`
// calls, in src/api/client.ts, never set that); bypass the proxy for those
// so Vite falls through to serving index.html instead.
const apiProxy = (target: string): ProxyOptions => ({
  target,
  bypass(req) {
    if (req.headers.accept?.includes("text/html")) return "/index.html";
  },
});

// Built assets are served by `crucible serve` from ui/dist at "/".
// In dev, proxy the API so the app can call same-origin paths.
export default defineConfig({
  plugins: [react()],
  base: "/",
  build: { outDir: "dist", emptyOutDir: true },
  server: {
    port: 5173,
    proxy: {
      "/runs": apiProxy("http://127.0.0.1:8787"),
      "/findings": apiProxy("http://127.0.0.1:8787"),
      "/wishes": apiProxy("http://127.0.0.1:8787"),
      "/health": apiProxy("http://127.0.0.1:8787"),
      "/config": apiProxy("http://127.0.0.1:8787"),
      "/openapi.json": apiProxy("http://127.0.0.1:8787"),
    },
  },
  test: {
    environment: "jsdom",
    globals: true,
  },
});
