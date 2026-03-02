from __future__ import annotations

from typing import Any, Dict, List
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse

from .DESCARGA_SIGED import (
    get_job_dir,
    progreso,
    start_download_if_free,
)

router = APIRouter()


def _validate_url(url: str) -> str:
    u = (url or "").strip()
    if not (u.startswith("http://") or u.startswith("https://")):
        raise HTTPException(status_code=400, detail="URL inválida: debe comenzar con http o https")
    return u


@router.post("/descargar")
async def descargar(payload: Dict[str, Any]) -> Dict[str, Any]:
    url = _validate_url(payload.get("url", ""))
    job_id = await start_download_if_free(url)  # genera job_id si no se pasa
    if not job_id:
        raise HTTPException(status_code=409, detail="Ya hay una descarga en curso")
    return {"ok": True, "job_id": job_id}


@router.get("/progreso")
async def progreso_actual() -> Dict[str, Any]:
    # En tu DESCARGA_SIGED.py el progreso se expone con snapshot() (async)
    return await progreso.snapshot()


@router.get("/archivos")
async def listar_archivos(job_id: str = Query(..., min_length=4)) -> Dict[str, Any]:
    job_dir = get_job_dir(job_id)
    if not job_dir.exists():
        raise HTTPException(status_code=404, detail="job_id no existe")

    files: List[str] = sorted([p.name for p in job_dir.iterdir() if p.is_file()])
    return {"ok": True, "job_id": job_id, "files": files}


@router.get("/archivos/{job_id}/{filename}")
async def descargar_archivo(job_id: str, filename: str):
    job_dir = get_job_dir(job_id)
    fpath = (job_dir / filename).resolve()

    # seguridad: evitar path traversal
    if job_dir.resolve() not in fpath.parents:
        raise HTTPException(status_code=400, detail="Nombre de archivo inválido")

    if not fpath.exists() or not fpath.is_file():
        raise HTTPException(status_code=404, detail="Archivo no encontrado")

    return FileResponse(
        path=str(fpath),
        filename=fpath.name,
        media_type="application/octet-stream",
    )