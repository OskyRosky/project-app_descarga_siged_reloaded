# app/routes.py
from __future__ import annotations

import re
from typing import Optional
from urllib.parse import urlparse, quote

import httpx
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.DESCARGA_SIGED import (
    start_download_if_free,
    cancel_descarga,
    is_running,
    progreso,
)

router = APIRouter()

# ============================
# Helpers de seguridad / SSRF
# ============================

_ALLOWED_HOST = "cgrweb.cgr.go.cr"


def _is_allowed_remote_url(raw: str) -> bool:
    try:
        u = urlparse(raw.strip())
        if u.scheme not in ("http", "https"):
            return False
        host = (u.hostname or "").lower()
        return host == _ALLOWED_HOST
    except Exception:
        return False


def _sanitize_filename(name: str) -> str:
    name = (name or "archivo").strip()
    # evita path traversal
    name = name.replace("\\", "_").replace("/", "_")
    # caracteres inválidos en Windows/macOS
    name = re.sub(r'[<>:"/\\|?*\x00-\x1F]+', "_", name).strip()
    return name or "archivo"


def _content_disposition_attachment(filename: str) -> str:
    """
    Usa filename* UTF-8 para soportar acentos/espacios.
    """
    safe = _sanitize_filename(filename)
    return f"attachment; filename*=UTF-8''{quote(safe)}"


# ============================
# Modelos
# ============================

class URLRequest(BaseModel):
    url: str


class ClienteDownloadedRequest(BaseModel):
    filename: str = ""


# ============================
# Endpoints existentes
# ============================

@router.post("/descargar")
async def descargar_archivos(req: URLRequest):
    url = (req.url or "").strip()

    # Validaciones rápidas (coinciden con la UX actual)
    if not url.lower().startswith(("http://", "https://")):
        raise HTTPException(status_code=400, detail="URL inválida: debe comenzar con http o https")
    if "cgrweb.cgr.go.cr" not in url.lower():
        raise HTTPException(status_code=400, detail="Dominio no permitido. Debe ser cgrweb.cgr.go.cr")

    if is_running():
        raise HTTPException(status_code=409, detail="Ya hay una descarga en curso")

    ok = await start_download_if_free(url)
    if not ok:
        raise HTTPException(status_code=409, detail="Ya hay una descarga en curso")

    return {"ok": True, "message": "Descarga iniciada"}


@router.get("/progreso")
async def obtener_progreso():
    return progreso.to_dict()


@router.get("/archivos")
async def obtener_archivos():
    """
    Devuelve la lista de archivos descubiertos por el backend:
      [{"name": "...", "url": "https://..."}]
    Nota: esto NO descarga nada en el servidor; el frontend descargará en el cliente.
    """
    return {"ok": True, "files": progreso.files}


@router.post("/cliente/descargado")
async def cliente_descargado(req: ClienteDownloadedRequest):
    await progreso.report_client_downloaded(filename=(req.filename or "").strip())
    return {"ok": True}


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


@router.post("/reset")
async def reset_progreso():
    """
    Reset manual.
    Nota: si hay una corrida activa, esto solo limpia el estado visual,
    pero NO detiene Playwright. Para eso usar /cancelar.
    """
    await progreso.reset()
    return {"ok": True}


# ============================
# NUEVO: Proxy streaming (anti-CORS)
# ============================

@router.get("/proxy")
async def proxy_descarga(
    url: str = Query(..., description="URL remoto (solo cgrweb.cgr.go.cr)"),
    name: Optional[str] = Query(None, description="Nombre sugerido para guardar"),
):
    """
    Stream del archivo remoto hacia el cliente:
    - NO guarda en disco del servidor
    - Evita CORS (porque el navegador llama a este endpoint del backend)
    - Permite fijar nombre con Content-Disposition

    Seguridad:
    - Allowlist estricta al host cgrweb.cgr.go.cr (anti-SSRF)
    """
    url = (url or "").strip()
    if not _is_allowed_remote_url(url):
        raise HTTPException(status_code=400, detail="URL no permitida para proxy (solo cgrweb.cgr.go.cr).")

    filename = _sanitize_filename(name or "archivo")
    cd = _content_disposition_attachment(filename)

    # Timeouts razonables para stream
    timeout = httpx.Timeout(connect=15.0, read=60.0, write=15.0, pool=15.0)

    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=timeout) as client:
            # stream GET
            resp = await client.get(url)
            if resp.status_code >= 400:
                raise HTTPException(status_code=resp.status_code, detail=f"Proxy error HTTP {resp.status_code}")

            content_type = resp.headers.get("content-type") or "application/octet-stream"

            async def iter_bytes():
                async for chunk in resp.aiter_bytes():
                    yield chunk

            headers = {
                "Content-Disposition": cd,
                # Si el upstream manda content-length lo pasamos (no siempre viene)
            }
            if "content-length" in resp.headers:
                headers["Content-Length"] = resp.headers["content-length"]

            return StreamingResponse(iter_bytes(), media_type=content_type, headers=headers)

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Proxy fallo: {e}")