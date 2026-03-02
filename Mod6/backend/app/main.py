from fastapi import FastAPI
from app.routes import router
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI()

# CORS para permitir conexión con el frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Podés restringir esto luego
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Incluir rutas
app.include_router(router)

@app.get("/")
def root():
    return {"message": "SIGED Reloaded Backend en ejecución"}