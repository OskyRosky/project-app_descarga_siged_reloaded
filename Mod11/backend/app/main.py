# app/main.py
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.routes import router

app = FastAPI(title="SIGED Downloader (Mod9)")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/health")
def health():
    return {"ok": True}

# 1) Primero API
app.include_router(router)

# 2) Luego frontend: servimos / con el build copiado en app/public
#    Como se monta al final, NO tapa las rutas del API.
app.mount("/", StaticFiles(directory="app/public", html=True), name="frontend")