from fastapi import FastAPI, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.responses import FileResponse
from pathlib import Path
from app.routes import router  # asegúrate que routes.py expone `router`

app = FastAPI(title="SIGED Reloaded Backend")

# CORS abierto para desarrollo (ajusta si necesitas)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Rutas de la API
app.include_router(router)

# Servir frontend compilado (copiado a /app/static en el Dockerfile)
STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
if STATIC_DIR.exists():
    app.mount("/assets", StaticFiles(directory=STATIC_DIR / "assets"), name="assets")

@app.get("/")
def index():
    index_file = STATIC_DIR / "index.html"
    if index_file.exists():
        return FileResponse(index_file)
    return {"message": "SIGED Reloaded Backend en ejecución"}

@app.get("/vite.svg")
def vite_svg():
    f = STATIC_DIR / "vite.svg"
    if f.exists():
        return FileResponse(f)
    return Response(status_code=404)

@app.get("/favicon.ico")
def favicon():
    f = STATIC_DIR / "favicon.ico"
    if f.exists():
        return FileResponse(f)
    return Response(status_code=404)