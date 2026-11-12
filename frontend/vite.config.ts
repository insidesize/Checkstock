import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// В разработке /api проксируется на бэкенд; в Docker фронт раздаёт сам FastAPI.
export default defineConfig({
  plugins: [react()],
  server: { port: 5173, proxy: { "/api": "http://localhost:8000" } },
});
