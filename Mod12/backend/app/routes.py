from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.DESCARGA_SIGED import (
    start_download_if_free,
    cancel_descarga,
    is_running,
    progreso,  # contiene el estado y el método reset()
)

router = APIRouter()


class URLRequest(BaseModel):
    url: str


@router.post("/descargar")
async def descargar_archivos(req: URLRequest):
    url = (req.url or "").strip()

    # Validaciones rápidas (coinciden con la UX actual)
    if not url.lower().startswith(("http://", "https://")):
        raise HTTPException(status_code=400, detail="URL inválida: debe comenzar con http o https")
    if "cgrweb.cgr.go.cr" not in url.lower():
        raise HTTPException(status_code=400, detail="Dominio no permitido. Debe ser cgrweb.cgr.go.cr")

    # Evitar doble ejecución
    if is_running():
        raise HTTPException(status_code=409, detail="Ya hay una descarga en curso")

    ok = await start_download_if_free(url)
    if not ok:
        raise HTTPException(status_code=409, detail="Ya hay una descarga en curso")

    return {"ok": True, "message": "Descarga iniciada"}


@router.get("/progreso")
async def obtener_progreso():
    return progreso.to_dict()


@router.post("/cancelar")
async def cancelar():
    ok = await cancel_descarga()
    if not ok:
        return {"ok": False, "message": "No hay descarga en curso"}
    return {"ok": True, "message": "Cancelación solicitada"}


# 🔹 NUEVO: resetear el estado en el backend para arrancar “limpio”
@router.post("/reset")
async def reset_progreso():
    # No pasamos URL → vuelve a "inicio", contadores en cero.
    await progreso.reset()
    return {"ok": True}