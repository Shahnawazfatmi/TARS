import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Dev: the screen runs on :5173 and reaches FATMEE's API through /api.
// Build: FATMEE serves the screen itself at http://127.0.0.1:8000/app/.
export default defineConfig({
  plugins: [react()],
  base: "/app/",
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8000",
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, ""),
      },
    },
  },
});
