import path from "node:path";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// During `npm run dev`, the React app talks to a manager running on
// :8080. We proxy /api so cookies / Authorization don't have to deal with
// CORS in dev. In prod the same FastAPI process serves both web/dist and
// /api/v1, so no proxy is needed.
const MANAGER_DEV_URL = process.env.PCT_MANAGER_URL ?? "http://localhost:8080";

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: MANAGER_DEV_URL,
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: "dist",
    sourcemap: true,
  },
});
