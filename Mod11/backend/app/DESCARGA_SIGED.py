import os
import re
import json
import asyncio
import uuid
import traceback
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional, List, Dict, Any

from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError


# =========================================================
#  Storage server-side por JOB
# =========================================================

def _safe_filename(name: str) -> str:
    """
    Sanitiza el nombre para evitar path traversal y caracteres raros.
    """
    name = (name or "").strip()
    name = name.replace("\\", "_").replace("/", "_")
    name = re.sub(r"[\x00-\x1f\x7f]+", "_", name)
    name = re.sub(r"\s+", " ", name).strip()
    if not name:
        name = "archivo.pdf"
    return name


def get_job_dir(job_id: str) -> Path:
    """Return job directory path and ensure it exists.

    Env: SIGED_STORAGE_DIR
      - In Docker: set to /data (and mount a volume if you want persistence).
      - Running locally: if not set, default to a writable folder: <backend>/data

    Final path:
      <SIGED_STORAGE_DIR>/SIGED_DOCUMENTOS/<job_id>
    """
    env_base = (os.getenv("SIGED_STORAGE_DIR", "").strip() or None)

    if env_base:
        base = Path(env_base).expanduser()
    else:
        # default local-safe path: Mod11/backend/data
        base = Path(__file__).resolve().parents[1] / "data"

    job_dir = base / "SIGED_DOCUMENTOS" / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    return job_dir


def ensure_job_dir(job_id: str) -> Path:
    """
    Crea el directorio del job si no existe y lo retorna.
    """
    d = get_job_dir(job_id)
    d.mkdir(parents=True, exist_ok=True)
    return d


def list_job_files(job_id: str) -> List[str]:
    """
    Lista archivos (solo files) dentro del job_dir.
    """
    job_dir = get_job_dir(job_id)
    if not job_dir.exists():
        return []
    files: List[str] = []
    for p in job_dir.iterdir():
        if p.is_file():
            files.append(p.name)
    files.sort()
    return files


def resolve_job_file(job_id: str, filename: str) -> Path:
    """
    Resuelve un archivo dentro del job dir de forma segura.
    Lanza ValueError si intenta salirse del directorio.
    """
    job_dir = get_job_dir(job_id).resolve()
    target = (job_dir / filename).resolve()
    if job_dir not in target.parents and target != job_dir:
        raise ValueError("Ruta inválida.")
    return target


# =========================================================
#  Validaciones básicas / config
# =========================================================

_ALLOWED_DOMAIN = "cgrweb.cgr.go.cr"


def _is_allowed_url(url: str) -> bool:
    url = (url or "").strip().lower()
    return url.startswith("http") and (_ALLOWED_DOMAIN in url)


def _decode_dialog_url(href: str) -> str:
    """
    En SIGED suelen haber enlaces tipo:
    #action$a-dialog-open?.... (encoded)
    Aquí intentamos extraer algo usable.
    En tu implementación original ya existía algo similar.
    """
    href = href or ""
    # Si ya te funciona tu decode actual, podés reemplazar esta función por la tuya.
    # Por ahora dejamos una versión conservadora:
    if "a-dialog-open" not in href:
        return ""
    return href


def _format_exc(e: Exception) -> str:
    """
    Devuelve un string corto + traceback para depurar errores del worker.
    Importante: esto evita que el handler de excepciones falle por NameError.
    """
    try:
        return "".join(traceback.format_exception(type(e), e, e.__traceback__)).strip()
    except Exception:
        return str(e)


# =========================================================
#  Estado / Progreso (1 job a la vez)
# =========================================================

@dataclass
class ProgresoState:
    ok: bool = True
    running: bool = False
    cancelado: bool = False

    job_id: Optional[str] = None
    url: Optional[str] = None

    total: int = 0
    done: int = 0
    last_file: Optional[str] = None

    status: str = "idle"  # idle|running|finalizado|error|cancelado
    error: Optional[str] = None

    files: List[str] = None  # se llena al final

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["files"] = self.files or []
        return d


class Progreso:
    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._state = ProgresoState(files=[])

    async def reset(self, url: str, job_id: str) -> None:
        async with self._lock:
            self._state = ProgresoState(
                ok=True,
                running=True,
                cancelado=False,
                job_id=job_id,
                url=url,
                total=0,
                done=0,
                last_file=None,
                status="running",
                error=None,
                files=[],
            )

    async def set_total(self, total: int) -> None:
        async with self._lock:
            self._state.total = int(total)

    async def inc_done(self, last_file: Optional[str] = None) -> None:
        async with self._lock:
            self._state.done += 1
            if last_file:
                self._state.last_file = last_file

    async def set_error(self, msg: str) -> None:
        async with self._lock:
            self._state.ok = False
            self._state.running = False
            self._state.status = "error"
            self._state.error = msg

    async def set_cancelado(self) -> None:
        async with self._lock:
            self._state.ok = False
            self._state.running = False
            self._state.cancelado = True
            self._state.status = "cancelado"

    async def set_finalizado(self) -> None:
        async with self._lock:
            self._state.running = False
            self._state.status = "finalizado"
            # snapshot files
            if self._state.job_id:
                self._state.files = list_job_files(self._state.job_id)

    async def snapshot(self) -> Dict[str, Any]:
        async with self._lock:
            return self._state.to_dict()


progreso = Progreso()

_current_task: Optional[asyncio.Task] = None
_cancel_event = asyncio.Event()

_current_job_id: Optional[str] = None
_current_job_dir: Optional[Path] = None


def get_current_job_id() -> Optional[str]:
    return _current_job_id


def get_current_job_dir() -> Optional[Path]:
    return _current_job_dir


def _can_start() -> bool:
    return (_current_task is None) or _current_task.done()


def is_running() -> bool:
    return (_current_task is not None) and (not _current_task.done())


async def cancel_descarga() -> bool:
    global _current_task
    if _current_task is None or _current_task.done():
        return False
    _cancel_event.set()
    return True


def _task_done_callback(task: asyncio.Task) -> None:
    """
    Captura excepciones no manejadas del background task.
    Si el task muere y no se llamó progreso.set_error(), al menos queda reflejado.
    """
    async def _mark_error(msg: str) -> None:
        await progreso.set_error(msg)

    try:
        exc = task.exception()
        if exc is not None:
            asyncio.create_task(_mark_error(f"Worker crashed:\n{_format_exc(exc)}"))
    except asyncio.CancelledError:
        pass
    except Exception as e:
        asyncio.create_task(_mark_error(f"Worker callback failed:\n{_format_exc(e)}"))


# =========================================================
#  API pública que usará routes.py
# =========================================================

async def start_download_if_free(url: str, job_id: Optional[str] = None) -> Optional[str]:
    """
    Inicia 1 job si no hay otro corriendo.
    Devuelve job_id si arrancó, None si está ocupado.
    """
    global _current_task, _current_job_id, _current_job_dir

    if not _can_start():
        return None

    jid = job_id or uuid.uuid4().hex[:10]
    _cancel_event.clear()

    _current_job_id = jid
    _current_job_dir = ensure_job_dir(jid)

    # Importante: el worker corre async y cualquier excepción debe quedar reflejada en progreso.error
    _current_task = asyncio.create_task(descargar_documentos(url=url, job_id=jid))
    _current_task.add_done_callback(_task_done_callback)

    return jid


# =========================================================
#  Descarga (Playwright) -> server-side job_dir
# =========================================================

async def _click_download_anywhere(page, scope_locator=None) -> bool:
    """
    Intenta encontrar y clickear un control de descarga.

    - Primero busca dentro de scope_locator (si existe)
    - Luego busca en la página
    - Luego busca dentro de frames/iframes (típico en APEX)
    """
    candidates = [
        'button:has-text("Descargar")',
        'a:has-text("Descargar")',
        'text=/descargar/i',
        'text=/download/i',
        'a[download]',
    ]

    async def _try_root(root) -> bool:
        for sel in candidates:
            loc = root.locator(sel)
            if await loc.count() > 0:
                try:
                    await loc.first.click()
                    return True
                except Exception:
                    # si el click falla, seguimos probando
                    continue
        return False

    # 1) scope (diálogo)
    if scope_locator is not None:
        if await _try_root(scope_locator):
            return True

    # 2) página completa
    if await _try_root(page):
        return True

    # 3) frames (APEX suele meter contenido en iframe dentro del diálogo)
    for fr in page.frames:
        try:
            for sel in candidates:
                loc = fr.locator(sel)
                if await loc.count() > 0:
                    await loc.first.click()
                    return True
        except Exception:
            continue

    return False


async def _download_from_page(page, job_dir: Path, timeout_ms: int, retries: int, scope=None) -> Optional[str]:
    """
    Intenta disparar descarga desde la UI actual.
    Retorna filename guardado si logró, o None si no hubo descarga.

    scope:
      - Si se pasa, debe ser un Locator que represente el diálogo/overlay.
      - Si no, busca en toda la página.

    Nota importante:
      - Usamos page.expect_download() siempre sobre page (aunque el click sea dentro del diálogo/iframe)
        porque el evento download lo emite el Page.
    """
    for _ in range(max(1, retries + 1)):
        if _cancel_event.is_set():
            return None

        try:
            async with page.expect_download(timeout=timeout_ms) as download_info:
                clicked = await _click_download_anywhere(page, scope_locator=scope)
                if not clicked:
                    # no encontró nada que clickear
                    return None

            download = await download_info.value
            suggested = _safe_filename(download.suggested_filename or "archivo.pdf")
            target = job_dir / suggested
            await download.save_as(str(target))
            return target.name

        except PlaywrightTimeoutError:
            # reintenta
            continue
        except Exception:
            # reintenta una vez más
            continue

    return None


async def _detect_dialog(page) -> Optional[Any]:
    """
    Detecta un diálogo/modal visible.
    APEX puede usar:
      - jQuery UI: .ui-dialog
      - Universal Theme: .t-Dialog
      - genérico: [role="dialog"]
    """
    candidates = [
        page.locator(".ui-dialog:visible"),
        page.locator(".t-Dialog:visible"),
        page.locator('div[role="dialog"]:visible'),
    ]
    for c in candidates:
        try:
            if await c.count() > 0:
                return c.first
        except Exception:
            continue
    return None


async def _close_dialog(page, dialog) -> None:
    """
    Cierra el diálogo/modal si existe.
    """
    try:
        close_btns = [
            dialog.locator('button[aria-label="Close"]'),
            dialog.locator(".ui-dialog-titlebar-close"),
            dialog.locator('button:has-text("Cerrar")'),
            dialog.locator('button:has-text("Close")'),
        ]
        for b in close_btns:
            try:
                if await b.count() > 0:
                    await b.first.click()
                    return
            except Exception:
                continue
        # fallback
        await page.keyboard.press("Escape")
    except Exception:
        pass


async def _open_target_and_download(page, link_locator, job_dir: Path, timeout_ms: int, retries: int) -> Optional[str]:
    """
    Abre el documento (vía diálogo o popup/tab) y trata de descargar.

    Casos que manejamos:
      A) click abre un diálogo (modal) en la misma page
      B) click abre un popup/tab (y vos ves "denegado" y se cierra): lo capturamos y descargamos desde ahí

    Retorna:
      - nombre del archivo si se descargó
      - None si no se pudo descargar para ese documento
    """
    if _cancel_event.is_set():
        return None

    popup = None
    dialog = None

    # 1) Click y tratar de capturar popup rápidamente (si existe)
    try:
        await link_locator.scroll_into_view_if_needed()

        # OJO: si no hay popup, esto hace timeout rápido y seguimos (sin fallar el job)
        try:
            async with page.expect_popup(timeout=1500) as pop_info:
                await link_locator.click(force=True)
            popup = await pop_info.value
        except Exception:
            # sin popup, el click igual ya se ejecutó arriba en muchos casos,
            # pero para estar seguros, hacemos un click (tolerante) si no hubo popup
            try:
                await link_locator.click(force=True)
            except Exception:
                return None

    except Exception:
        return None

    # 2) Si hubo popup/tab, trabajamos en él
    if popup is not None:
        try:
            try:
                await popup.wait_for_load_state("domcontentloaded", timeout=15_000)
            except Exception:
                pass

            fname = await _download_from_page(
                page=popup,
                job_dir=job_dir,
                timeout_ms=timeout_ms,
                retries=retries,
                scope=None,
            )

            # cerrar popup para seguir
            try:
                await popup.close()
            except Exception:
                pass

            return fname

        except Exception:
            try:
                await popup.close()
            except Exception:
                pass
            return None

    # 3) Si no hubo popup, esperamos detectar diálogo en la misma page
    try:
        # pequeño wait para que APEX renderice el modal
        for _ in range(60):  # ~12s
            if _cancel_event.is_set():
                return None
            dialog = await _detect_dialog(page)
            if dialog is not None:
                break
            await asyncio.sleep(0.2)

        if dialog is None:
            return None

        fname = await _download_from_page(
            page=page,
            job_dir=job_dir,
            timeout_ms=timeout_ms,
            retries=retries,
            scope=dialog,
        )

        await _close_dialog(page, dialog)
        return fname

    except Exception:
        return None


async def descargar_documentos(url: str, job_id: str) -> None:
    """
    Flujo principal de descarga.
    Guarda en server-side: <SIGED_STORAGE_DIR>/SIGED_DOCUMENTOS/<job_id>/
    """
    await progreso.reset(url=url, job_id=job_id)

    if not _is_allowed_url(url):
        await progreso.set_error("URL inválida o dominio no permitido (cgrweb.cgr.go.cr).")
        return

    job_dir = ensure_job_dir(job_id)

    HEADLESS = os.getenv("SIGED_HEADLESS", "0") == "1"
    GOTO_TIMEOUT_MS = int(os.getenv("SIGED_GOTO_TIMEOUT_MS", "90000"))
    FILE_TIMEOUT_MS = int(os.getenv("SIGED_FILE_TIMEOUT_MS", "30000"))
    RETRIES = int(os.getenv("SIGED_FILE_RETRIES", "2"))

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
                locale="es-CR",
            )
            page = await context.new_page()

            try:
                await page.goto(url, timeout=GOTO_TIMEOUT_MS)
            except PlaywrightTimeoutError:
                await progreso.set_error("Timeout cargando la página principal.")
                await browser.close()
                return

            # APEX a veces carga contenido asíncrono; esto ayuda a estabilizar
            try:
                await page.wait_for_load_state("domcontentloaded", timeout=15_000)
            except Exception:
                pass

            # Localiza los enlaces 'Ver Documento' (hash-dialog)
            doc_links = page.locator('a[href^="#action$a-dialog-open?"]')
            total = await doc_links.count()
            if total == 0:
                await progreso.set_error("No se encontraron enlaces de documentos (#action$a-dialog-open?).")
                await browser.close()
                return

            await progreso.set_total(total)

            for i in range(total):
                if _cancel_event.is_set():
                    await progreso.set_cancelado()
                    await browser.close()
                    return

                link = doc_links.nth(i)

                # En tu versión anterior se intentaba "decodificar" href.
                # Mantengo la lectura (por si la usás para debug), pero la descarga
                # ocurre haciendo click real sobre el link en la página.
                try:
                    href = await link.get_attribute("href")
                    _ = _decode_dialog_url(href or "")
                except Exception:
                    pass

                fname = await _open_target_and_download(
                    page=page,
                    link_locator=link,
                    job_dir=job_dir,
                    timeout_ms=FILE_TIMEOUT_MS,
                    retries=RETRIES,
                )

                if fname:
                    await progreso.inc_done(last_file=fname)
                else:
                    # diferenciamos si no hubo diálogo vs no hubo descarga
                    dialog_now = await _detect_dialog(page)
                    if dialog_now is None:
                        await progreso.inc_done(last_file=f"(sin dialog {i+1})")
                    else:
                        await progreso.inc_done(last_file=f"(sin archivo {i+1})")
                        await _close_dialog(page, dialog_now)

            try:
                await browser.close()
            except Exception:
                pass

            await progreso.set_finalizado()

    except Exception as e:
        await progreso.set_error(f"Fallo inesperado:\n{_format_exc(e)}")
        return