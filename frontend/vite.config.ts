import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
export default defineConfig({
  plugins: [react()],
  build: {
    outDir: "../web",
    emptyOutDir: true,
    rollupOptions: {
      output: {
        manualChunks: {
          charts: ["recharts"],
          tables: ["@tanstack/react-table"],
        },
      },
    },
  },
  server: { proxy: { "/api": "http://127.0.0.1:8082" } },
});
