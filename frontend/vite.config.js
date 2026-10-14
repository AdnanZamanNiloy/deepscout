import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

/* `base` is configurable so ONE build works both locally (dev/preview at "/")
 * and on a host that serves the site under a subpath (e.g. a GitHub project
 * page at "/<repo>/"). Set VITE_BASE at build time to override; it defaults to
 * "/", which is what Cloudflare Pages uses. DocsView reads the same value via
 * import.meta.env.BASE_URL, so the docs iframe stays in sync automatically. */
export default defineConfig({
  base: process.env.VITE_BASE || "/",
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8000",
        changeOrigin: true,
      },
    },
  },
  preview: {
    port: 4173,
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8000",
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: "dist",
    /* Demo mode ships as a static bundle with no backend; a single vendor
     * chunk keeps the initial load snappy on the free tier. */
    chunkSizeWarningLimit: 900,
  },
});
