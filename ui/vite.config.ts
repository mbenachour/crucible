import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Built assets are served by `crucible serve` from ui/dist at "/".
// In dev, proxy the API so the app can call same-origin paths.
export default defineConfig({
  plugins: [react()],
  base: "/",
  build: { outDir: "dist", emptyOutDir: true },
  server: {
    port: 5173,
    proxy: {
      "/runs": "http://127.0.0.1:8787",
      "/findings": "http://127.0.0.1:8787",
      "/wishes": "http://127.0.0.1:8787",
      "/health": "http://127.0.0.1:8787",
      "/openapi.json": "http://127.0.0.1:8787",
    },
  },
  test: {
    environment: "jsdom",
    globals: true,
  },
});
