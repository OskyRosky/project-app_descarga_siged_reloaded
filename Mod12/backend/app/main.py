# app/main.py
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.routes import router

app = FastAPI(title="SIGED Downloader (Mod12)")

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

# 1) API
app.include_router(router)

# 2) Frontend build en app/public (si existe)
app.mount("/", StaticFiles(directory="app/public", html=True), name="frontend")