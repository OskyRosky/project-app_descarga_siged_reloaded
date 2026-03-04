import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

/*
  Proxy del frontend hacia el backend FastAPI.

  Todas estas rutas van al backend:
  - /descargar
  - /progreso
  - /archivos
  - /cliente/*
  - /reset
  - /cancelar

  Backend actual:
  http://127.0.0.1:8210
*/

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      "/descargar": "http://127.0.0.1:8210",
      "/progreso": "http://127.0.0.1:8210",
      "/archivos": "http://127.0.0.1:8210",
      "/cliente": "http://127.0.0.1:8210",
      "/reset": "http://127.0.0.1:8210",
      "/cancelar": "http://127.0.0.1:8210",
    },
  },
});