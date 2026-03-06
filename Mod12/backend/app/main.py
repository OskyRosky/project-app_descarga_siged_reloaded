from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.routes import router

# ============================================================
#  APP
# ============================================================
# Monolítico: 1 solo servicio sirve:
#   1) API (FastAPI)  -> /descargar, /progreso, /proxy, etc.
#   2) Frontend (Vite build) -> archivos estáticos + SPA fallback
#
# Objetivo:
#   - El frontend llama a la API en el MISMO ORIGEN (same-origin),
#     por ejemplo: http://<host>:8210/descargar
#   - Esto reduce problemas de CORS y simplifica Docker/ECS.
# ============================================================
app = FastAPI(title="SIGED Downloader (Mod12)")

# ============================================================
#  HEALTHCHECK (útil para ALB/ECS/monitoreo)
# ============================================================
@app.get("/health")
def health():
    return {"ok": True}

# ============================================================
#  API ROUTES
# ============================================================
# Importante: incluir la API ANTES de montar el frontend
# para que las rutas /descargar, /progreso, /proxy, etc.
# no sean "tapadas" por el StaticFiles/SPA fallback.
app.include_router(router)

# ============================================================
#  FRONTEND: servir build estático de Vite
# ============================================================
# En este enfoque, el build del frontend (Vite dist) se copia a:
#   backend/app/public/
#
# Estructura esperada:
#   backend/app/public/
#     index.html
#     assets/...
#
# Si no existe index.html, NO se monta frontend (la API igual funciona).
PUBLIC_DIR = Path(__file__).resolve().parent / "public"
INDEX_HTML = PUBLIC_DIR / "index.html"

if PUBLIC_DIR.exists() and INDEX_HTML.exists():
    # 1) Servir archivos estáticos (assets, js, css, etc.)
    #    Con html=True, "/" entrega index.html automáticamente.
    app.mount("/", StaticFiles(directory=str(PUBLIC_DIR), html=True), name="frontend")

    # 2) SPA fallback:
    #    - Si el usuario refresca una ruta o navega directo (ej: /algo),
    #      devolvemos index.html para que React/Vite maneje el routing.
    #
    # OJO: esto solo aplica a GET. La API ya está registrada arriba.
    @app.get("/{full_path:path}")
    async def spa_fallback(full_path: str):
        return FileResponse(str(INDEX_HTML))