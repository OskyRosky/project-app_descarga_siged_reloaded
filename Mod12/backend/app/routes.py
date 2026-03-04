# app/routes.py
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.DESCARGA_SIGED import (
    start_download_if_free,
    cancel_descarga,
    is_running,
    progreso,
)

router = APIRouter()


class URLRequest(BaseModel):
    url: str


class ClienteDownloadedRequest(BaseModel):
    filename: str = ""


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

    # Start (ahora hace el reset/start adentro + blindaje)
    ok = await start_download_if_free(url)
    if not ok:
        raise HTTPException(status_code=409, detail="Ya hay una descarga en curso")

    return {"ok": True, "message": "Descarga iniciada"}


@router.get("/progreso")
async def obtener_progreso():
    return progreso.to_dict()


# 🔹 retorna el manifest (lista de archivos descubiertos)
@router.get("/archivos")
async def obtener_archivos():
    """
    Devuelve la lista de archivos descubiertos por el backend:
      [{"name": "...", "url": "https://..."}]
    Nota: esto NO descarga nada en el servidor; el frontend descargará en el cliente.
    """
    return {"ok": True, "files": progreso.files}


# 🔹 el cliente reporta que descargó 1 archivo (para progreso combinado A)
@router.post("/cliente/descargado")
async def cliente_descargado(req: ClienteDownloadedRequest):
    await progreso.report_client_downloaded(filename=(req.filename or "").strip())
    return {"ok": True}


# 🔹 el cliente marca finalizado (opcional, pero recomendado)
@router.post("/cliente/finalizar")
async def cliente_finalizar():
    await progreso.set_finalizado()
    return {"ok": True}


@router.post("/cancelar")
async def cancelar():
    ok = await cancel_descarga()
    if not ok:
        return {"ok": False, "message": "No hay descarga en curso"}
    return {"ok": True, "message": "Cancelación solicitada"}


# 🔹 resetear el estado en el backend para arrancar “limpio”
@router.post("/reset")
async def reset_progreso():
    """
    Reset manual.
    Nota: si hay una corrida activa, esto solo limpia el estado visual,
    pero NO detiene Playwright. Para eso usar /cancelar.
    """
    await progreso.reset()
    return {"ok": True}