import { defineConfig } from "vite";

const backend = "http://127.0.0.1:8088";

export default defineConfig({
  server: { proxy: { "/api": backend, "/guide": backend } },
  build: {
    rollupOptions: {
      output: {
        manualChunks: { charts: ["recharts"], react: ["react", "react-dom"] },
      },
    },
  },
});
