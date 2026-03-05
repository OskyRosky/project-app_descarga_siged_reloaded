# app/routes.py
from typing import Optional
from urllib.parse import unquote, urlparse

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
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


def _is_allowed_proxy_url(raw_url: str) -> bool:
    try:
        u = urlparse(raw_url)
        host = (u.netloc or "").lower()
        return u.scheme in ("http", "https") and host.endswith("cgrweb.cgr.go.cr")
    except Exception:
        return False


@router.post("/descargar")
async def descargar_archivos(req: URLRequest):
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
    return progreso.to_dict()


@router.get("/archivos")
async def obtener_archivos():
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
    await progreso.reset()
    return {"ok": True}


@router.api_route("/proxy", methods=["GET", "HEAD"])
async def proxy(request: Request, url: str, name: Optional[str] = None):
    target_url = unquote((url or "").strip())

    if not target_url:
        raise HTTPException(status_code=400, detail="Falta parámetro url")

    if not _is_allowed_proxy_url(target_url):
        raise HTTPException(status_code=400, detail="URL no permitida para proxy (solo cgrweb.cgr.go.cr)")

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

    timeout = httpx.Timeout(connect=20.0, read=120.0, write=20.0, pool=20.0)

    async with httpx.AsyncClient(follow_redirects=True, timeout=timeout) as client:
        try:
            if request.method == "HEAD":
                r = await client.head(target_url, headers=headers)
                out_headers = {}
                for k in ("content-type", "content-disposition", "content-length", "accept-ranges", "etag", "last-modified"):
                    if k in r.headers:
                        out_headers[k] = r.headers[k]
                if name and "content-disposition" not in out_headers:
                    safe = name.replace('"', "")
                    out_headers["content-disposition"] = f'attachment; filename="{safe}"'
                return StreamingResponse(iter([b""]), status_code=r.status_code, headers=out_headers)

            r = await client.stream("GET", target_url, headers=headers)

            out_headers = {}
            for k in ("content-type", "content-disposition", "content-length", "accept-ranges", "etag", "last-modified"):
                if k in r.headers:
                    out_headers[k] = r.headers[k]
            if name and "content-disposition" not in out_headers:
                safe = name.replace('"', "")
                out_headers["content-disposition"] = f'attachment; filename="{safe}"'

            async def _aiter():
                async for chunk in r.aiter_bytes(chunk_size=1024 * 256):
                    yield chunk

            return StreamingResponse(_aiter(), status_code=r.status_code, headers=out_headers)

        except httpx.RequestError as e:
            raise HTTPException(status_code=502, detail=f"Proxy error: {type(e).__name__}")