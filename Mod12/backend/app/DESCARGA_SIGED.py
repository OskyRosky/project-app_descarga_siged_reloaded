# app/DESCARGA_SIGED.py
import os
import re
import asyncio
import unicodedata
from pathlib import Path
from typing import Optional, List, Tuple, Dict, Any
from urllib.parse import unquote, urlparse, urljoin

from platformdirs import user_downloads_dir
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError

# ============================
#  Estado de progreso global
# ============================

class ProgresoDescarga:
    """
    Estado global y seguro (vía asyncio.Lock).

    ⚠️ Importante (SIGED Reloaded - descarga en CLIENTE):
    - El backend YA NO guarda archivos en disco del servidor.
    - El backend hace "descubrimiento" (manifest): nombre + URL directa de descarga.
    - El frontend descarga secuencialmente en la PC del usuario.

    Estados (status): inicio | descubriendo | esperando_descarga_cliente | finalizado | error | cancelado
    """
    def __init__(self) -> None:
        self._lock = asyncio.Lock()

        # Estado macro
        self.status: str = "inicio"        # inicio | descubriendo | esperando_descarga_cliente | finalizado | error | cancelado
        self.phase: str = "idle"           # idle | discovery | client_download
        self.current_url: str = ""

        # Métricas de descubrimiento (backend)
        self.total: int = 0                # total de documentos/adjuntos esperados (según tabla)
        self.discovered_done: int = 0      # cuántos descubrimos (uno a uno)

        # Métricas de descarga (cliente)
        self.client_done: int = 0          # cuántos archivos el cliente reporta como descargados

        # UI
        self.percent: int = 0              # porcentaje combinado discovery + client (A: una sola barra)
        self.last_file: str = ""
        self.last_error: str = ""

        # Manifest descubierto (en memoria)
        self.files: List[Dict[str, str]] = []   # [{"name": "...", "url": "https://..."}]
        self._seen_urls: set[str] = set()       # para evitar duplicados

    async def reset(self, url: str = "") -> None:
        async with self._lock:
            self.status = "inicio"
            self.phase = "idle"
            self.current_url = url

            self.total = 0
            self.discovered_done = 0
            self.client_done = 0

            self.percent = 0
            self.last_file = ""
            self.last_error = ""

            self.files = []
            self._seen_urls = set()

    async def start(self, url: str) -> None:
        async with self._lock:
            self.current_url = url
            self.status = "descubriendo"
            self.phase = "discovery"
            self.last_error = ""
            self.last_file = ""
            self._recalc_percent_locked()

    async def set_total(self, total: int) -> None:
        async with self._lock:
            self.total = max(int(total), 0)
            self._recalc_percent_locked()

    async def add_discovered(self, name: str, url: str) -> None:
        """
        Registra un archivo descubierto (backend).
        - No descarga el archivo.
        - Solo guarda (name, url) en memoria.
        """
        async with self._lock:
            clean_url = (url or "").strip()
            if clean_url and clean_url not in self._seen_urls:
                self._seen_urls.add(clean_url)
                self.files.append({"name": name, "url": clean_url})

            self.discovered_done += 1
            self.last_file = name or self.last_file
            self._recalc_percent_locked()

    async def inc_discovered_placeholder(self, label: str) -> None:
        """Cuenta un 'documento atendido' aunque no se haya logrado extraer URL final."""
        async with self._lock:
            self.discovered_done += 1
            self.last_file = label
            self._recalc_percent_locked()

    async def set_waiting_client(self) -> None:
        """
        Termina el discovery y deja listo para que el CLIENTE comience a descargar.
        """
        async with self._lock:
            self.status = "esperando_descarga_cliente"
            self.phase = "client_download"
            self._recalc_percent_locked()

    async def report_client_downloaded(self, filename: str = "") -> None:
        """
        Endpoint del frontend (fase 2): el cliente reporta '1 archivo descargado'.
        Con esto mantenemos una sola barra de progreso (A).
        """
        async with self._lock:
            self.client_done += 1
            if filename:
                self.last_file = filename
            self._recalc_percent_locked()

    async def set_finalizado(self) -> None:
        async with self._lock:
            self.status = "finalizado"
            self.phase = "idle"
            # Al final, forzamos 100%
            self.percent = 100
            # Normalizamos contadores para consistencia
            # (si el cliente no reportó, client_done puede quedar en 0; el frontend mostrará su propio estado)
            self.last_error = ""

    async def set_error(self, msg: str) -> None:
        async with self._lock:
            self.status = "error"
            self.phase = "idle"
            self.last_error = msg
            self._recalc_percent_locked()

    async def set_cancelado(self) -> None:
        async with self._lock:
            self.status = "cancelado"
            self.phase = "idle"
            self._recalc_percent_locked()

    def _recalc_percent_locked(self) -> None:
        """
        Progreso combinado (A):
        - 0%..50% = discovery (backend)
        - 50%..100% = descarga cliente (frontend)
        Mantiene 1 barra estable sin reinicios.
        """
        if self.total <= 0:
            self.percent = 0
            return

        # discovery (0..50)
        d = min(max(self.discovered_done, 0), self.total) / self.total
        # client (0..50) sobre el mismo total
        c = min(max(self.client_done, 0), self.total) / self.total

        combined = (d * 50.0) + (c * 50.0)
        self.percent = int(round(min(max(combined, 0.0), 100.0)))

    def to_dict(self) -> Dict[str, Any]:
        # Lectura sin lock por simplicidad (se actualiza en bloque bajo lock)
        return {
            "ok": True,
            "status": self.status,
            "phase": self.phase,
            "url": self.current_url,

            # Totales y avances (expuestos para UI)
            "total": self.total,
            "discovered_done": self.discovered_done,
            "client_done": self.client_done,
            "percent": self.percent,

            "last_file": self.last_file,
            "last_error": self.last_error,

            # info del manifest (sin exponer todo si no lo piden)
            "files_count": len(self.files),
        }


# Instancias/globales de coordinación
progreso = ProgresoDescarga()
_current_task: Optional[asyncio.Task] = None
_cancel_event = asyncio.Event()

# ============================
#  Utilidades (nombres/archivos)
# ============================

def _is_allowed_url(url: str) -> bool:
    try:
        u = urlparse(url)
        return u.scheme in ("http", "https") and (u.netloc or "").lower().endswith("cgrweb.cgr.go.cr")
    except Exception:
        return False

def _sanitize_filename(name: str) -> str:
    name = name or "archivo"
    # quita tildes/acentos raros
    nfkd = unicodedata.normalize("NFKD", name)
    name = "".join([c for c in nfkd if not unicodedata.combining(c)])
    # reemplaza caracteres no seguros
    name = re.sub(r"[^\w\-.() ]+", "_", name).strip()
    # evita nombres vacíos
    return name or "archivo"

def _filename_from_content_disposition(cd: str) -> Optional[str]:
    if not cd:
        return None
    # filename*=UTF-8''xxx or filename="xxx"
    m = re.search(r"filename\*\s*=\s*UTF-8''([^;]+)", cd, re.IGNORECASE)
    if m:
        return unquote(m.group(1)).strip().strip('"')
    m = re.search(r'filename\s*=\s*"([^"]+)"', cd, re.IGNORECASE)
    if m:
        return m.group(1).strip()
    m = re.search(r"filename\s*=\s*([^;]+)", cd, re.IGNORECASE)
    if m:
        return m.group(1).strip().strip('"')
    return None

async def _guess_filename_via_headers(page, url: str) -> str:
    """
    Intenta obtener filename vía HEAD o Range GET (1 byte) para evitar bajar todo el archivo.
    Si falla, usa el basename del URL.
    """
    fallback = Path(urlparse(url).path).name or "archivo"
    try:
        # 1) HEAD (si lo soporta)
        resp = await page.request.fetch(url, method="HEAD")
        cd = (resp.headers or {}).get("content-disposition") or (resp.headers or {}).get("Content-Disposition")
        fn = _filename_from_content_disposition(cd or "")
        if fn:
            return _sanitize_filename(fn)
    except Exception:
        pass

    try:
        # 2) GET con Range para traer mínimo (si servidor lo soporta)
        resp = await page.request.fetch(url, method="GET", headers={"Range": "bytes=0-0"})
        cd = (resp.headers or {}).get("content-disposition") or (resp.headers or {}).get("Content-Disposition")
        fn = _filename_from_content_disposition(cd or "")
        if fn:
            return _sanitize_filename(fn)
    except Exception:
        pass

    return _sanitize_filename(fallback)

def _get_download_base() -> Path:
    """
    Legacy: base de descarga (ANTES se usaba para guardar en SIGED_DOCUMENTOS).
    Se conserva para orden y por si luego se requiere modo "proxy server" o debug.
    """
    try:
        # Prioridad: variable de entorno
        env = os.getenv("SIGED_DOWNLOAD_BASE", "").strip()
        if env:
            return Path(env).expanduser()
        # Default: Downloads del usuario
        return Path(user_downloads_dir())
    except Exception:
        return Path.home() / "Downloads"


# ============================
#  Descarga por respuesta/ página (como en test.py)
# ============================
# ⚠️ Nota: este bloque existía para "descargar y guardar" en disco.
# En SIGED Reloaded (descarga en CLIENTE), lo mantenemos como referencia/legacy.
# El flujo principal ahora usa "descubrimiento" (manifest) y NO escribe archivos.

async def _http_get_with_retry(page, url: str, retries: int = 2):
    last = None
    for _ in range(max(retries, 1)):
        try:
            resp = await page.request.get(url)
            if resp.ok:
                return resp
            last = Exception(f"HTTP {resp.status} for {url}")
        except Exception as e:
            last = e
        await asyncio.sleep(0.3)
    raise last or Exception("HTTP error")

async def _save_from_response(resp, download_dir: Path) -> Optional[str]:
    """
    Legacy: guarda un response a disco.
    Se mantiene por compatibilidad/orden (NO se usa en el flujo de manifest).
    """
    try:
        if not resp.ok:
            return None
        cd = resp.headers.get("content-disposition", "") or resp.headers.get("Content-Disposition", "")
        name = _filename_from_content_disposition(cd) or Path(urlparse(resp.url).path).name or "archivo"
        name = _sanitize_filename(name)
        data = await resp.body()
        out = download_dir / name
        out.write_bytes(data)
        return name
    except Exception:
        return None

async def _discover_from_page(page) -> Optional[Tuple[str, str]]:
    """
    Descubre (sin guardar) un URL directo a archivo en la página actual.
    Devuelve (filename, url).
    """
    # 1) visor (embed/object/iframe) -> src suele ser un get_blob / getfile directo
    for sel in ["embed", "object", "iframe"]:
        el = page.locator(sel)
        if await el.count():
            src = await el.first.get_attribute("src")
            if src:
                u = urljoin(page.url, src)
                name = await _guess_filename_via_headers(page, u)
                return name, u

    # 2) enlaces típicos (mismos candidates que antes, pero sin request.get/save)
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

        # Intentamos inferir nombre sin bajar el archivo completo
        name = await _guess_filename_via_headers(page, u)
        return name, u

    return None

# ============================
#  Navegación APEX por diálogos (hash-dialog)
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

async def _dump_links(page) -> List[str]:
    """Debug opcional: lista href visibles."""
    links = []
    a = page.locator("a")
    n = await a.count()
    for i in range(min(n, 200)):
        href = await a.nth(i).get_attribute("href")
        if href:
            links.append(href)
    return links

async def _crawl_dialog_chain(context, start_url: str, max_depth: int = 3) -> Optional[Tuple[str, str]]:
    """
    Abre una cadena de diálogos APEX (hasta max_depth) y trata de descubrir un link directo a archivo.
    Devuelve (name, url) si se pudo.
    """
    page = await context.new_page()
    try:
        await page.goto(start_url, timeout=60000)

        # Intento directo en la página actual
        found = await _discover_from_page(page)
        if found:
            return found

        # Si no hay link directo, buscamos más diálogos encadenados (hash)
        for _ in range(max_depth):
            # si hay otro enlace dialog-open, seguimos
            dialog_links = page.locator('a[href^="#action$a-dialog-open?"]')
            if await dialog_links.count() == 0:
                break

            href = await dialog_links.first.get_attribute("href")
            nxt = _decode_dialog_url(href or "")
            if not nxt:
                break

            await page.goto(nxt, timeout=60000)
            found = await _discover_from_page(page)
            if found:
                return found

        return None
    finally:
        try:
            await page.close()
        except Exception:
            pass


# ============================
#  API para routes.py
# ============================

def _can_start() -> bool:
    return (_current_task is None) or _current_task.done()

def is_running() -> bool:
    return (_current_task is not None) and (not _current_task.done())

def _on_task_done(task: asyncio.Task) -> None:
    """
    Blindaje:
    - Limpia referencia global cuando termina.
    - Si explotó con excepción, setea error en progreso (sin tumbar el server).
    """
    global _current_task
    _current_task = None
    try:
        exc = task.exception()
        if exc is not None:
            # No podemos "await" aquí; lanzamos una tarea para set_error.
            asyncio.create_task(progreso.set_error(f"Fallo inesperado: {exc}"))
    except asyncio.CancelledError:
        # Cancelled: estado lo setea cancel_descarga / worker.
        pass
    except Exception as e:
        asyncio.create_task(progreso.set_error(f"Fallo inesperado: {e}"))

async def start_download_if_free(url: str) -> bool:
    """
    Blindaje + Fix del "reset":
    - El reset/start ocurre SOLO aquí (cuando el endpoint acepta iniciar).
    - El worker NO resetea el estado.
    """
    global _current_task
    if not _can_start():
        return False

    # Prepara estado (una sola vez por corrida)
    await progreso.reset(url=url)

    if not _is_allowed_url(url):
        await progreso.set_error("URL inválida o dominio no permitido (cgrweb.cgr.go.cr).")
        return True  # aceptamos la llamada pero deja error listo para UI

    await progreso.start(url)

    _cancel_event.clear()
    _current_task = asyncio.create_task(descargar_documentos(url))
    _current_task.add_done_callback(_on_task_done)
    return True

async def cancel_descarga() -> bool:
    global _current_task
    if _current_task is None or _current_task.done():
        return False
    _cancel_event.set()
    return True


# ============================
#  Flujo principal de descarga
# ============================
# ⚠️ En SIGED Reloaded (servidor x86), esta función ahora hace:
#   1) discovery/manifest (backend)  -> progreso.status="descubriendo"
#   2) espera descargas del cliente  -> progreso.status="esperando_descarga_cliente"
# El frontend se encargará de descargar a la PC del usuario y reportar progreso por API.

async def descargar_documentos(url: str) -> None:
    """
    Flujo principal (backend). Actualiza `progreso`.
    Respeta cancelación (_cancel_event).

    Nuevo comportamiento:
    - NO escribe archivos en disco.
    - Descubre lista de adjuntos (name + download_url).

    Nota importante (blindaje):
    - reset/start YA se hicieron en start_download_if_free().
    - Este worker asume que el estado ya está preparado.
    """
    # Headless: por defecto "1" (servidor/x86 no tiene UI)
    HEADLESS = os.getenv("SIGED_HEADLESS", "1") == "1"
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
                accept_downloads=False,  # ya no usamos downloads del servidor
                user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
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
                    await progreso.inc_discovered_placeholder(f"(no decodificado {i+1})")
                    continue

                found = await _crawl_dialog_chain(context, real, max_depth=3)
                if found:
                    name, durl = found
                    await progreso.add_discovered(name=name, url=durl)
                else:
                    await progreso.inc_discovered_placeholder(f"(sin archivo {i+1})")

            await browser.close()

            # Pasamos a fase: esperar que el cliente descargue secuencialmente
            await progreso.set_waiting_client()

    except Exception as e:
        await progreso.set_error(f"Fallo inesperado: {e}")
        return