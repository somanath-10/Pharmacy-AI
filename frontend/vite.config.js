import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    host: "0.0.0.0",
    port: 4109,
    proxy: {
      "/api": { target: "http://localhost:4108", changeOrigin: true },
      "/health": { target: "http://localhost:4108", changeOrigin: true },
    },
  },
  preview: {
    port: 4173,
    host: "0.0.0.0",
    port: 4109,
    proxy: {
      "/api": { target: "http://localhost:4108", changeOrigin: true },
      "/health": { target: "http://localhost:4108", changeOrigin: true },
    },
  },
  build: { outDir: "dist" },
});
