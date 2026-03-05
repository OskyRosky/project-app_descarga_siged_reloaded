# app/routes.py
from __future__ import annotations

# ============================
# Imports
# ============================

import re
from typing import Optional
from urllib.parse import quote, unquote, urlparse

import httpx
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from starlette.background import BackgroundTask

# Importamos el “motor” del backend:
# - start_download_if_free(): arranca discovery (Playwright) si no hay job corriendo
# - cancel_descarga(): solicita cancelación
# - is_running(): indica si hay job activo
# - progreso: estado compartido (status, files, counters, etc.)
from app.DESCARGA_SIGED import (
    start_download_if_free,
    cancel_descarga,
    is_running,
    progreso,
)

router = APIRouter()

# ============================
# Seguridad (anti-SSRF) para /proxy
# ============================
# Solo permitimos proxyear URLs cuyo host sea EXACTAMENTE cgrweb.cgr.go.cr.
_ALLOWED_HOST = "cgrweb.cgr.go.cr"


# ============================
# Modelos Pydantic (payloads)
# ============================

class URLRequest(BaseModel):
    # Body para /descargar
    url: str


class ClienteDownloadedRequest(BaseModel):
    # Body para /cliente/descargado
    filename: str = ""


# ============================
# Helpers (validación y sanitización)
# ============================

def _is_allowed_remote_url(raw: str) -> bool:
    """
    Valida que la URL sea http(s) y que el host sea exactamente cgrweb.cgr.go.cr
    Esto protege /proxy contra SSRF (ej: intentar proxyear 169.254.169.254).
    """
    try:
        u = urlparse(raw.strip())
        if u.scheme not in ("http", "https"):
            return False
        host = (u.hostname or "").lower()
        return host == _ALLOWED_HOST
    except Exception:
        return False


def _sanitize_filename(name: str) -> str:
    """
    Limpia el nombre sugerido para evitar:
    - Path traversal (../)
    - Separadores de ruta
    - Caracteres inválidos (Windows/macOS)
    """
    name = (name or "archivo").strip()
    name = name.replace("\\", "_").replace("/", "_")
    name = re.sub(r'[<>:"/\\|?*\x00-\x1F]+', "_", name).strip()
    return name or "archivo"


def _content_disposition_attachment(filename: str) -> str:
    """
    Construye Content-Disposition para forzar descarga y soportar UTF-8
    (acentos/espacios) usando filename*.
    """
    safe = _sanitize_filename(filename)
    return f"attachment; filename*=UTF-8''{quote(safe)}"


# ============================
# Endpoints: Orquestación del flujo
# ============================

@router.post("/descargar")
async def descargar_archivos(req: URLRequest):
    """
    Inicia el proceso backend de discovery (Playwright):
    - valida URL y dominio
    - evita doble ejecución
    - arranca start_download_if_free(url)
    """
    url = (req.url or "").strip()

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
    """
    Devuelve el estado global:
    status, phase, percent, counters, last_error, etc.
    """
    return progreso.to_dict()


@router.get("/archivos")
async def obtener_archivos():
    """
    Devuelve el “manifest” descubierto por el backend:
      {"ok": true, "files": [{"name": "...", "url": "https://..."}, ...]}

    Importante:
    - NO descarga en el servidor
    - el cliente (frontend) hará la descarga usando /proxy
    """
    return {"ok": True, "files": progreso.files}


@router.post("/cliente/descargado")
async def cliente_descargado(req: ClienteDownloadedRequest):
    """
    El cliente reporta que descargó un archivo:
    - sirve para sumar progreso del lado cliente
    """
    await progreso.report_client_downloaded(filename=(req.filename or "").strip())
    return {"ok": True}


@router.post("/cliente/finalizar")
async def cliente_finalizar():
    """
    El cliente indica que ya terminó la descarga total.
    """
    await progreso.set_finalizado()
    return {"ok": True}


@router.post("/cancelar")
async def cancelar():
    """
    Solicita cancelación del proceso en backend (Playwright).
    """
    ok = await cancel_descarga()
    if not ok:
        return {"ok": False, "message": "No hay descarga en curso"}
    return {"ok": True, "message": "Cancelación solicitada"}


@router.post("/reset")
async def reset_progreso():
    """
    Limpia estado del progreso en backend (visual).
    No necesariamente detiene Playwright: para eso usar /cancelar.
    """
    await progreso.reset()
    return {"ok": True}


# ============================
# /proxy: Streaming anti-CORS (clave del modelo “descarga en el cliente”)
# ============================
# El navegador no puede bajar directo desde cgrweb... por CORS.
# Entonces:
# - el frontend pide al backend /proxy?url=...&name=...
# - el backend hace fetch al servidor remoto y “streamea” al cliente
# - NO guarda nada en disco del server
#
# Importante: el bug anterior era cerrar el AsyncClient antes de que termine el stream.
# Aquí lo resolvemos manteniendo vivo el stream y cerrando al final con BackgroundTask.

@router.api_route("/proxy", methods=["GET", "HEAD"])
async def proxy(
    request: Request,
    url: str = Query(...),
    name: Optional[str] = Query(None),
):
    target_url = unquote((url or "").strip())

    # Validaciones básicas
    if not target_url:
        raise HTTPException(status_code=400, detail="Falta parámetro url")
    if not _is_allowed_remote_url(target_url):
        raise HTTPException(status_code=400, detail="URL no permitida para proxy (solo cgrweb.cgr.go.cr)")

    # Nombre sugerido y Content-Disposition
    filename = _sanitize_filename(name or "archivo")
    cd = _content_disposition_attachment(filename)

    # Copiamos headers útiles (Range / cache validators) hacia upstream
    incoming_range = request.headers.get("range")
    incoming_if_modified = request.headers.get("if-modified-since")
    incoming_if_none_match = request.headers.get("if-none-match")

    headers = {}
    if incoming_range:
        headers["range"] = incoming_range
    if incoming_if_modified:
        headers["if-modified-since"] = incoming_if_modified
    if incoming_if_none_match:
        headers["if-none-match"] = incoming_if_none_match

    # Cliente HTTP asíncrono (timeout alto en read para archivos)
    timeout = httpx.Timeout(connect=20.0, read=180.0, write=20.0, pool=20.0)
    client = httpx.AsyncClient(follow_redirects=True, timeout=timeout)

    try:
        # ============================
        # HEAD: solo metadatos (sin body)
        # ============================
        if request.method == "HEAD":
            r = await client.head(target_url, headers=headers)

            out_headers = {}
            for k in ("content-type", "content-length", "accept-ranges", "etag", "last-modified"):
                if k in r.headers:
                    out_headers[k] = r.headers[k]

            # Forzamos nombre
            out_headers["content-disposition"] = cd

            await client.aclose()
            return StreamingResponse(iter([b""]), status_code=r.status_code, headers=out_headers)

        # ============================
        # GET: streaming real
        # ============================
        # Importantísimo:
        # client.stream(...) devuelve un context manager async.
        # Necesitamos mantenerlo abierto durante TODO el StreamingResponse.
        stream_cm = client.stream("GET", target_url, headers=headers)
        r = await stream_cm.__aenter__()  # entramos al context manager y obtenemos response vivo

        # Headers de salida (passthrough razonable)
        out_headers = {}
        for k in ("content-type", "content-length", "accept-ranges", "etag", "last-modified", "content-range"):
            if k in r.headers:
                out_headers[k] = r.headers[k]

        out_headers["content-disposition"] = cd

        # Cierre garantizado cuando termina el stream (o si el cliente corta)
        async def _close():
            try:
                await stream_cm.__aexit__(None, None, None)
            finally:
                await client.aclose()

        # Iterador async que va “empujando” bytes hacia el cliente
        async def _aiter():
            async for chunk in r.aiter_bytes(chunk_size=1024 * 256):
                yield chunk

        return StreamingResponse(
            _aiter(),
            status_code=r.status_code,
            media_type=out_headers.get("content-type", "application/octet-stream"),
            headers=out_headers,
            background=BackgroundTask(_close),
        )

    except httpx.RequestError:
        await client.aclose()
        raise HTTPException(status_code=502, detail="Proxy error (httpx)")

    except Exception as e:
        await client.aclose()
        raise HTTPException(status_code=500, detail=f"Proxy fallo: {type(e).__name__}: {e}")