from __future__ import annotations

import os
from typing import Any, Dict, List, Optional
from uuid import uuid4
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel

from app.DESCARGA_SIGED import (
    start_download_if_free,
    cancel_descarga,
    is_running,
    get_job_dir,
    progreso,  # puede ser objeto con to_dict() o estructura equivalente
)

router = APIRouter()


class URLRequest(BaseModel):
    url: str


def _progress_to_dict() -> Dict[str, Any]:
    """
    Hace el endpoint /progreso robusto aunque 'progreso' cambie internamente.
    """
    try:
        if progreso is None:
            return {"estado": "inicio", "total": 0, "descargados": 0, "porcentaje": 0}

        # Caso típico: progreso.to_dict()
        if hasattr(progreso, "to_dict") and callable(getattr(progreso, "to_dict")):
            return progreso.to_dict()

        # Si ya fuera un dict
        if isinstance(progreso, dict):
            return progreso

        # Fallback: intentar serializar atributos simples
        d = {}
        for k in ("estado", "total", "descargados", "porcentaje", "mensaje", "error"):
            if hasattr(progreso, k):
                d[k] = getattr(progreso, k)
        return d or {"estado": "desconocido"}
    except Exception as e:
        # Nunca reventar el endpoint por el progreso
        return {"estado": "error", "error": f"Error generando progreso: {e!s}"}


def _validate_url(url: str) -> str:
    url = (url or "").strip()
    if not url.lower().startswith(("http://", "https://")):
        raise HTTPException(status_code=400, detail="URL inválida: debe comenzar con http o https")
    if "cgrweb.cgr.go.cr" not in url.lower():
        raise HTTPException(status_code=400, detail="Dominio no permitido. Debe ser cgrweb.cgr.go.cr")
    return url


def _safe_job_id(job_id: str) -> str:
    job_id = (job_id or "").strip()
    if not job_id or len(job_id) > 64:
        raise HTTPException(status_code=400, detail="job_id inválido")
    # evitar path traversal
    if any(x in job_id for x in ("/", "\\", "..")):
        raise HTTPException(status_code=400, detail="job_id inválido")
    return job_id


def _safe_filename(filename: str) -> str:
    filename = (filename or "").strip()
    if not filename or len(filename) > 255:
        raise HTTPException(status_code=400, detail="filename inválido")
    # evitar path traversal
    if any(x in filename for x in ("/", "\\", "..")):
        raise HTTPException(status_code=400, detail="filename inválido")
    return filename


@router.post("/descargar")
async def descargar_archivos(req: URLRequest):
    """
    Inicia descarga y devuelve job_id.
    """
    url = _validate_url(req.url)

    # Evitar doble ejecución (modo mínimo: 1 job a la vez)
    if is_running():
        raise HTTPException(status_code=409, detail="Ya hay una descarga en curso")

    job_id = uuid4().hex[:10]

    try:
        jid = await start_download_if_free(url, job_id=job_id)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error iniciando descarga: {e!s}")

    if not jid:
        raise HTTPException(status_code=409, detail="Ya hay una descarga en curso")

    return {"ok": True, "job_id": jid}


@router.get("/progreso")
async def obtener_progreso():
    return _progress_to_dict()


@router.post("/cancelar")
async def cancelar():
    try:
        ok = await cancel_descarga()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error cancelando: {e!s}")

    if not ok:
        return {"ok": False, "message": "No hay descarga en curso"}
    return {"ok": True, "message": "Cancelación solicitada"}


@router.get("/archivos")
async def listar_archivos(job_id: str = Query(..., description="job_id devuelto por /descargar")):
    """
    Lista archivos de un job: GET /archivos?job_id=...
    """
    job_id = _safe_job_id(job_id)
    job_dir: Path = get_job_dir(job_id)

    if not job_dir.exists():
        raise HTTPException(status_code=404, detail="job_id no encontrado")

    try:
        files = sorted([p.name for p in job_dir.iterdir() if p.is_file()])
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error listando archivos: {e!s}")

    return {"ok": True, "job_id": job_id, "files": files}


@router.get("/archivos/{job_id}/{filename}")
async def descargar_archivo(job_id: str, filename: str):
    """
    Descarga 1 archivo: GET /archivos/{job_id}/{filename}
    """
    job_id = _safe_job_id(job_id)
    filename = _safe_filename(filename)

    job_dir: Path = get_job_dir(job_id)
    file_path = job_dir / filename

    if not file_path.exists() or not file_path.is_file():
        raise HTTPException(status_code=404, detail="Archivo no encontrado")

    # Fuerza descarga en el navegador
    return FileResponse(
        path=str(file_path),
        filename=filename,
        media_type="application/octet-stream",
    )