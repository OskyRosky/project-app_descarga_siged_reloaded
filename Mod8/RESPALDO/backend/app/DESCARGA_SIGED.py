import os
import re
import asyncio
import unicodedata
from pathlib import Path
from typing import Optional, List
from urllib.parse import unquote, urlparse

from platformdirs import user_downloads_dir
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

    async def reset(self, url: str = "") -> None:
        async with self._lock:
            self.status = "inicio"
            self.total = 0
            self.done = 0
            self.percent = 0
            self.last_file = ""
            self.last_error = ""
            self.current_url = url

    async def start(self, url: str) -> None:
        async with self._lock:
            self.status = "descargando"
            self.total = 0
            self.done = 0
            self.percent = 0
            self.last_file = ""
            self.last_error = ""
            self.current_url = url

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
        # Lectura sin lock por simplicidad (se actualiza en bloque bajo lock)
        return {
            "status": self.status,
            "total": self.total,
            "done": self.done,
            "percent": self.percent,
            "last_file": self.last_file,
            "last_error": self.last_error,
            "current_url": self.current_url,
        }

# Instancias/globales de coordinación
progreso = ProgresoDescarga()
_cancel_event = asyncio.Event()
_current_task: Optional[asyncio.Task] = None

# ============================
#  Utilidades
# ============================

def _sanitize_filename(filename: str) -> str:
    filename = unquote(filename or "")
    filename = unicodedata.normalize("NFKD", filename).encode("ASCII", "ignore").decode("ASCII")
    filename = re.sub(r'[<>:"/\\|?*]', "", filename)
    filename = filename.strip()
    if not filename:
        filename = "documento.pdf"
    return filename

async def _get_filename_from_headers(response) -> Optional[str]:
    try:
        cd = response.headers.get("content-disposition", "") or ""
    except Exception:
        cd = ""
    m = re.search(r'filename\*?=["\']?(?:UTF-8["\']*)?([^";]+)', cd, re.IGNORECASE)
    if m:
        return _sanitize_filename(m.group(1).strip())
    return None

def _is_allowed_url(url: str) -> bool:
    try:
        p = urlparse(url)
        if p.scheme not in ("http", "https"):
            return False
        return (p.netloc or "").lower() == "cgrweb.cgr.go.cr"
    except Exception:
        return False

# --- NUEVO: helper con reintentos y timeout para HTTP GET de archivos ---
async def _http_get_with_retry(request, url: str, *, retries: int = 2, timeout_ms: int = 30000):
    """
    Intenta GET con hasta `retries` reintentos (total = 1 + retries).
    Lanza la última excepción si todas fallan.
    """
    last_err = None
    for attempt in range(1 + retries):
        try:
            return await request.get(url, timeout=timeout_ms)
        except Exception as e:
            last_err = e
            # pequeña espera incremental
            await asyncio.sleep(0.8 * (attempt + 1))
    raise last_err  # si agotamos los intentos

# ============================
#  API para routes.py
# ============================

def _can_start() -> bool:
    return (_current_task is None) or _current_task.done()

def is_running() -> bool:
    """
    True si HAY una descarga en curso.
    """
    return (_current_task is not None) and (not _current_task.done())

async def start_download_if_free(url: str) -> bool:
    """
    Lanza la descarga en segundo plano si no hay otra en curso.
    True si inició; False si ya había una en curso.
    """
    global _current_task
    if not _can_start():
        return False
    _cancel_event.clear()
    _current_task = asyncio.create_task(descargar_documentos(url))
    return True

async def cancel_descarga() -> bool:
    """
    Señala cancelación; la tarea activa comprobará _cancel_event y se detendrá.
    """
    global _current_task
    if _current_task is None or _current_task.done():
        return False
    _cancel_event.set()
    return True

# ============================
#  Descarga principal
# ============================

async def descargar_documentos(url: str) -> None:
    """
    Flujo principal de descarga. Actualiza `progreso`.
    Respeta cancelación (_cancel_event).
    """
    await progreso.reset(url=url)

    if not _is_allowed_url(url):
        await progreso.set_error("URL inválida o dominio no permitido (cgrweb.cgr.go.cr).")
        return

    await progreso.start(url)

    ruta_descarga = Path(user_downloads_dir()) / "SIGED_DOCUMENTOS"
    try:
        ruta_descarga.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        await progreso.set_error(f"No se pudo crear carpeta de descarga: {e}")
        return

    HEADLESS = os.getenv("SIGED_HEADLESS", "0") == "1"

    # timeouts configurables (seguros por defecto)
    GOTO_TIMEOUT_MS = int(os.getenv("SIGED_GOTO_TIMEOUT_MS", "90000"))
    FILE_TIMEOUT_MS = int(os.getenv("SIGED_FILE_TIMEOUT_MS", "30000"))
    RETRIES = int(os.getenv("SIGED_FILE_RETRIES", "2"))  # reintentos extras (total = 1 + RETRIES)

    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=HEADLESS,
                args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-blink-features=AutomationControlled"],
            )
            context = await browser.new_context(
                accept_downloads=True,
                user_agent=("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                            "AppleWebKit/537.36 (KHTML, like Gecko) "
                            "Chrome/124.0.0.0 Safari/537.36"),
                locale="es-CR",
            )
            page = await context.new_page()

            try:
                await page.goto(url, timeout=GOTO_TIMEOUT_MS)
            except PlaywrightTimeoutError:
                await progreso.set_error("Timeout cargando la página principal.")
                await browser.close()
                return

            # Enlaces de documentos
            try:
                anchors = await page.locator("a").all()
                links: List = []
                for a in anchors:
                    try:
                        href = await a.get_attribute("href")
                    except Exception:
                        href = None
                    if href and "apex.navigation.dialog" in href:
                        links.append(a)
            except Exception as e:
                await progreso.set_error(f"Error obteniendo enlaces: {e}")
                await browser.close()
                return

            if not links:
                await progreso.set_error("No se encontraron enlaces de documentos.")
                await browser.close()
                return

            await progreso.set_total(len(links))

            base_url = "https://cgrweb.cgr.go.cr/apex/"
            for idx, link in enumerate(links, start=1):
                if _cancel_event.is_set():
                    await progreso.set_cancelado()
                    await browser.close()
                    return
                try:
                    async with context.expect_page() as new_page_info:
                        await link.click()
                    new_page = await new_page_info.value
                    await new_page.wait_for_load_state("load")
                    await new_page.wait_for_timeout(3000)
                except Exception as e:
                    await progreso.set_error(f"Error abriendo documento {idx}: {e}")
                    await browser.close()
                    return

                try:
                    embed = new_page.locator("embed")
                    if await embed.count() > 0:
                        file_src = await embed.get_attribute("src")
                        full_url = file_src if file_src and file_src.startswith("http") else (base_url + (file_src or ""))

                        # --- descarga con reintentos + timeout ---
                        file_response = await _http_get_with_retry(
                            new_page.request, full_url, retries=RETRIES, timeout_ms=FILE_TIMEOUT_MS
                        )
                        file_name = await _get_filename_from_headers(file_response)
                        if not file_name:
                            file_name = f"Documento_{idx}.pdf"
                        content = await file_response.body()

                        out_path = ruta_descarga / file_name
                        out_path.write_bytes(content)

                        # verificación mínima de tamaño
                        try:
                            size = out_path.stat().st_size
                        except Exception:
                            size = 0
                        if size == 0:
                            # marca el archivo como incompleto pero avanza
                            out_path.rename(out_path.with_name(out_path.stem + "_incompleto" + out_path.suffix))

                        await progreso.inc_done(last_file=file_name)
                    else:
                        # sin <embed> igual marcamos avance para no atascar la barra
                        await progreso.inc_done(last_file="")
                except Exception as e:
                    # En vez de abortar todo, marcamos error del documento y continuamos al siguiente
                    await progreso.inc_done(last_file=f"(error doc {idx})")
                    # guardamos el último error informativo (no cambiamos a estado global error)
                    await progreso.set_error(f"Documento {idx} con error: {e}")
                finally:
                    try:
                        await new_page.close()
                    except Exception:
                        pass

            await browser.close()
            await progreso.set_finalizado()

    except Exception as e:
        await progreso.set_error(f"Fallo inesperado: {e}")
        return