import os
import re
import asyncio
import unicodedata
from pathlib import Path
from typing import Optional, List, Tuple
from urllib.parse import unquote, urlparse, urljoin
from uuid import uuid4

from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError

# ============================
#  Estado de progreso global
# ============================

class ProgresoDescarga:
    """
    Estado global y seguro (vía asyncio.Lock).
    Estados: inicio | descargando | finalizado | error | cancelado
    """
    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self.status: str = "inicio"        # inicio | descargando | finalizado | error | cancelado
        self.total: int = 0
        self.done: int = 0
        self.percent: int = 0
        self.last_file: str = ""
        self.last_error: str = ""
        self.current_url: str = ""
        self.current_job_id: str = ""

    async def reset(self, url: str = "", job_id: str = "") -> None:
        async with self._lock:
            self.status = "inicio"
            self.total = 0
            self.done = 0
            self.percent = 0
            self.last_file = ""
            self.last_error = ""
            self.current_url = url
            self.current_job_id = job_id

    async def start(self, url: str, job_id: str) -> None:
        async with self._lock:
            self.status = "descargando"
            self.total = 0
            self.done = 0
            self.percent = 0
            self.last_file = ""
            self.last_error = ""
            self.current_url = url
            self.current_job_id = job_id

    async def set_total(self, n: int) -> None:
        async with self._lock:
            self.total = int(max(0, n))
            self.percent = int((self.done * 100) / self.total) if self.total > 0 else 0

    async def inc_done(self, last_file: str = "") -> None:
        async with self._lock:
            self.done += 1
            if last_file:
                self.last_file = last_file
            self.percent = int((self.done * 100) / self.total) if self.total > 0 else (100 if self.done > 0 else 0)

    async def set_finalizado(self) -> None:
        async with self._lock:
            self.status = "finalizado"
            if self.total == 0:
                self.percent = 100

    async def set_error(self, msg: str) -> None:
        async with self._lock:
            self.status = "error"
            self.last_error = str(msg)

    async def set_cancelado(self) -> None:
        async with self._lock:
            self.status = "cancelado"

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "total": self.total,
            "done": self.done,
            "percent": self.percent,
            "last_file": self.last_file,
            "last_error": self.last_error,
            "current_url": self.current_url,
            "job_id": self.current_job_id,
        }

# Instancias/globales de coordinación
progreso = ProgresoDescarga()
_cancel_event = asyncio.Event()
_current_task: Optional[asyncio.Task] = None

# Estado actual del job (para routes.py)
_current_job_id: Optional[str] = None
_current_job_dir: Optional[Path] = None

# ============================
#  Utilidades (nombres/archivos)
# ============================

def _sanitize_filename(filename: str) -> str:
    filename = unquote(filename or "")
    base, ext = os.path.splitext(filename)
    base = unicodedata.normalize("NFKD", base).encode("ASCII", "ignore").decode("ASCII")
    base = re.sub(r'[<>:"/\\|?*\x00-\x1F]', "", base).strip().rstrip(".")
    ext  = re.sub(r'[<>:"/\\|?*\x00-\x1F]', "", ext)
    if not base:
        base = "archivo"
    return f"{base}{ext}"

def _sanitize_job_id(job_id: str) -> str:
    # solo letras, números, guion y underscore
    job_id = (job_id or "").strip()
    job_id = re.sub(r"[^a-zA-Z0-9_-]", "", job_id)
    return job_id or uuid4().hex[:10]

def _unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    i = 1
    while True:
        cand = path.with_name(f"{path.stem} ({i}){path.suffix}")
        if not cand.exists():
            return cand
        i += 1

def _parse_filename_from_cd(cd: str) -> Optional[str]:
    if not cd:
        return None
    m = re.search(r"filename\*\s*=\s*UTF-8''([^;]+)", cd, flags=re.I)
    if m:
        return _sanitize_filename(unquote(m.group(1)))
    m = re.search(r'filename\s*=\s*"([^"]+)"', cd, flags=re.I)
    if m:
        return _sanitize_filename(m.group(1))
    m = re.search(r'filename\s*=\s*([^;]+)', cd, flags=re.I)
    if m:
        return _sanitize_filename(m.group(1).strip())
    return None

# mapeo básico por content-type → extensión
_CT_EXT = {
    "application/pdf": ".pdf",
    "application/msword": ".doc",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    "application/vnd.ms-excel": ".xls",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
    "application/zip": ".zip",
    "application/octet-stream": "",  # genérico
}
def _ext_for_content_type(ct: str) -> str:
    if not ct:
        return ""
    ct = ct.split(";")[0].strip().lower()
    return _CT_EXT.get(ct, "")

def _is_allowed_url(url: str) -> bool:
    try:
        p = urlparse(url)
        if p.scheme not in ("http", "https"):
            return False
        return (p.netloc or "").lower() == "cgrweb.cgr.go.cr"
    except Exception:
        return False

# ============================
#  Server-side storage
# ============================

def _get_storage_base() -> Path:
    """
    Directorio base en servidor/contendor.
    Env: SIGED_STORAGE_DIR (default: /data)
    """
    base = os.getenv("SIGED_STORAGE_DIR", "/data").strip() or "/data"
    return Path(base).expanduser().resolve()

def get_job_dir(job_id: str) -> Path:
    """
    Ruta: <SIGED_STORAGE_DIR>/SIGED_DOCUMENTOS/<job_id>
    """
    job_id = _sanitize_job_id(job_id)
    return _get_storage_base() / "SIGED_DOCUMENTOS" / job_id

def list_job_files(job_id: str) -> List[str]:
    """
    Lista archivos dentro del job dir (solo files, no carpetas).
    Ordenado por nombre.
    """
    d = get_job_dir(job_id)
    if not d.exists() or not d.is_dir():
        return []
    return sorted([p.name for p in d.iterdir() if p.is_file()])

def get_current_job_id() -> Optional[str]:
    return _current_job_id

def get_current_job_dir() -> Optional[str]:
    return str(_current_job_dir) if _current_job_dir else None

# ============================
#  Descarga por respuesta/ página
# ============================

async def _save_from_response(response, download_dir: Path) -> Optional[str]:
    """Guarda si parece archivo descargable; retorna el nombre si guardó."""
    headers = response.headers or {}
    url = response.url
    ct = (headers.get("content-type", "") or "").lower()

    looks_file = (
        "application/" in ct
        or any(url.lower().split("?", 1)[0].endswith(suf) for suf in (".pdf", ".doc", ".docx", ".xls", ".xlsx", ".zip"))
        or "content-disposition" in {k.lower(): v for k, v in headers.items()}
    )
    if not looks_file:
        return None

    body = await response.body()
    cd = headers.get("content-disposition", "")
    fname = _parse_filename_from_cd(cd)

    if not fname:
        part = url.split("?", 1)[0].rstrip("/").split("/")[-1] or "archivo"
        if "." not in part:
            ext = _ext_for_content_type(ct)
            if ext and not part.endswith(ext):
                part += ext
            elif not ext:
                part += ".bin"
        fname = _sanitize_filename(part)

    path = _unique_path(download_dir / fname)
    path.write_bytes(body)

    # marca descargas vacías
    try:
        if path.stat().st_size == 0:
            path.rename(path.with_name(path.stem + "_incompleto" + path.suffix))
    except Exception:
        pass

    return path.name

async def _try_download_from_page(page, download_dir: Path) -> Optional[str]:
    """Intenta localizar visor o links directos en la página actual y descargar; devuelve nombre si guardó."""
    # 1) visor (embed/object/iframe)
    for sel in ["embed", "object", "iframe"]:
        el = page.locator(sel)
        if await el.count():
            src = await el.first.get_attribute("src")
            if src:
                u = urljoin(page.url, src)
                resp = await page.request.get(u)
                name = await _save_from_response(resp, download_dir)
                if name:
                    return name

    # 2) enlaces típicos
    candidates = page.locator(
        'a[href*="get_blob"], a[href*="getfile"], a[href*="download"], '
        'a:has-text("Descargar"), a:has-text("Ver"), '
        'a.t-Button:has-text("Descargar"), a.t-Button:has-text("Ver")'
    )
    n = await candidates.count()
    for i in range(n):
        a = candidates.nth(i)
        href = await a.get_attribute("href")
        if not href:
            continue
        u = urljoin(page.url, href)
        try:
            resp = await page.request.get(u)
            name = await _save_from_response(resp, download_dir)
            if name:
                return name
        except Exception:
            pass

    # 3) escucha respuestas tras un click genérico
    done = asyncio.Event()
    saved: Optional[str] = None

    async def listener(resp):
        nonlocal saved
        name = await _save_from_response(resp, download_dir)
        if name:
            saved = name
            done.set()

    page.on("response", listener)
    try:
        if n:
            await candidates.first.click(timeout=5000)
            try:
                await asyncio.wait_for(done.wait(), timeout=8)
                if saved:
                    return saved
            except asyncio.TimeoutError:
                pass
    finally:
        try:
            page.off("response", listener)
        except Exception:
            pass

    return None

# ============================
#  Navegación APEX por diálogos
# ============================

_BASE = "https://cgrweb.cgr.go.cr/apex/"

def _decode_dialog_url(hash_href: str) -> Optional[str]:
    """#action$a-dialog-open?url=f%3Fp%3D108%3A630... → https://.../apex/f?p=108:630:..."""
    if not hash_href or not hash_href.startswith("#action$a-dialog-open"):
        return None
    q = hash_href.split("?", 1)[-1]
    parts = dict(x.split("=", 1) for x in q.split("&") if "=" in x)
    enc = parts.get("url")
    if not enc:
        return None
    inner = unquote(enc)  # "f?p=108:630:..."
    return urljoin(_BASE, inner)

async def _crawl_dialog_chain(context, start_url: str, download_dir: Path, max_depth: int = 3) -> int:
    """Abre start_url y sigue #action$a-dialog-open?... hasta max_depth. Devuelve #archivos descargados."""
    downloads = 0
    to_visit: List[Tuple[str, int]] = [(start_url, 1)]
    seen = set()

    while to_visit:
        if _cancel_event.is_set():
            break

        url, depth = to_visit.pop(0)
        if (url, depth) in seen:
            continue
        seen.add((url, depth))

        page = await context.new_page()
        try:
            await page.goto(url, timeout=60_000)
            try:
                await page.wait_for_load_state("domcontentloaded", timeout=10_000)
            except PlaywrightTimeoutError:
                pass

            # intento directo de descarga en esta página
            name = await _try_download_from_page(page, download_dir)
            if name:
                downloads += 1
                await progreso.inc_done(last_file=name)

            # recolecta nuevos diálogos para profundizar
            if depth < max_depth:
                modal_links = page.locator('a[href^="#action$a-dialog-open?"]')
                n = await modal_links.count()
                for i in range(n):
                    href = await modal_links.nth(i).get_attribute("href")
                    real = _decode_dialog_url(href or "")
                    if real:
                        to_visit.append((real, depth + 1))
        finally:
            try:
                await page.close()
            except Exception:
                pass

    return downloads

# ============================
#  API para routes.py
# ============================

def _can_start() -> bool:
    return (_current_task is None) or _current_task.done()

def is_running() -> bool:
    return (_current_task is not None) and (not _current_task.done())

async def start_download_if_free(url: str, job_id: Optional[str] = None) -> Optional[str]:
    """
    Inicia descarga si está libre.
    Retorna job_id si inició, o None si ya hay un job corriendo.
    """
    global _current_task, _current_job_id, _current_job_dir

    if not _can_start():
        return None

    _cancel_event.clear()

    jid = _sanitize_job_id(job_id or uuid4().hex[:10])
    jdir = get_job_dir(jid)

    # crea carpetas
    try:
        jdir.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        await progreso.reset(url=url, job_id=jid)
        await progreso.set_error(f"No se pudo crear carpeta de storage: {e}")
        return None

    _current_job_id = jid
    _current_job_dir = jdir

    _current_task = asyncio.create_task(descargar_documentos(url, jid))
    return jid

async def cancel_descarga() -> bool:
    global _current_task
    if _current_task is None or _current_task.done():
        return False
    _cancel_event.set()
    return True

# ============================
#  Flujo principal de descarga
# ============================

async def descargar_documentos(url: str, job_id: str) -> None:
    """
    Flujo principal de descarga. Actualiza `progreso`.
    Respeta cancelación (_cancel_event).
    Guarda en server-side: <SIGED_STORAGE_DIR>/SIGED_DOCUMENTOS/<job_id>/
    """
    global _current_job_id, _current_job_dir

    job_id = _sanitize_job_id(job_id)
    _current_job_id = job_id
    _current_job_dir = get_job_dir(job_id)

    await progreso.reset(url=url, job_id=job_id)

    if not _is_allowed_url(url):
        await progreso.set_error("URL inválida o dominio no permitido (cgrweb.cgr.go.cr).")
        return

    await progreso.start(url, job_id)

    # directorio de descarga del job
    ruta_descarga = get_job_dir(job_id)
    try:
        ruta_descarga.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        await progreso.set_error(f"No se pudo crear carpeta de descarga: {e}")
        return

    HEADLESS = os.getenv("SIGED_HEADLESS", "0") == "1"
    GOTO_TIMEOUT_MS = int(os.getenv("SIGED_GOTO_TIMEOUT_MS", "90000"))

    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=HEADLESS,
                args=[
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-blink-features=AutomationControlled",
                ],
            )
            context = await browser.new_context(
                accept_downloads=True,
                user_agent=("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                            "AppleWebKit/537.36 (KHTML, like Gecko) "
                            "Chrome/124.0.0.0 Safari/537.36"),
                locale="es-CR",
            )
            page = await context.new_page()

            # Carga principal
            try:
                await page.goto(url, timeout=GOTO_TIMEOUT_MS)
            except PlaywrightTimeoutError:
                await progreso.set_error("Timeout cargando la página principal.")
                await browser.close()
                return

            # Localiza los enlaces 'Ver Documento' (hash-dialog)
            doc_links = page.locator('a[href^="#action$a-dialog-open?"]')
            total = await doc_links.count()
            if total == 0:
                await progreso.set_error("No se encontraron enlaces de documentos (#action$a-dialog-open?).")
                await browser.close()
                return

            await progreso.set_total(total)

            # Itera sobre cada documento
            for i in range(total):
                if _cancel_event.is_set():
                    await progreso.set_cancelado()
                    await browser.close()
                    return

                href = await doc_links.nth(i).get_attribute("href")
                real = _decode_dialog_url(href or "")
                if not real:
                    await progreso.inc_done(last_file=f"(no decodificado {i+1})")
                    continue

                # Explora la cadena de diálogos hasta 3 niveles y trata de descargar
                before = progreso.done
                _ = await _crawl_dialog_chain(context, real, ruta_descarga, max_depth=3)
                after = progreso.done

                # Si no se descargó nada dentro de la cadena, igualmente marcamos el doc como atendido
                if after == before:
                    await progreso.inc_done(last_file=f"(sin archivo {i+1})")

            await browser.close()
            await progreso.set_finalizado()

    except Exception as e:
        await progreso.set_error(f"Fallo inesperado: {e}")
        return