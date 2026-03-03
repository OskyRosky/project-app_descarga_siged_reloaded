import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Asegura que /descargar y /progreso vayan al backend (127.0.0.1:8000)
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      "/descargar": "http://127.0.0.1:8000",
      "/progreso": "http://127.0.0.1:8000",
    },
  },
});