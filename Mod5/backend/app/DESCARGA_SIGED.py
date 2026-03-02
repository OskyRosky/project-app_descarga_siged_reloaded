import sys
import asyncio
import os
import re
import unicodedata
from urllib.parse import unquote
from pathlib import Path
from platformdirs import user_downloads_dir
from playwright.async_api import async_playwright


def sanitize_filename(filename: str) -> str:
    filename = unquote(filename)
    filename = unicodedata.normalize("NFKD", filename).encode("ASCII", "ignore").decode("ASCII")
    filename = re.sub(r'[<>:"/\\|?*]', "", filename)
    return filename.strip()


async def get_filename_from_headers(response):
    content_disposition = response.headers.get("content-disposition", "")
    match = re.search(r'filename\*?=["\']?(?:UTF-8["\']*)?([^";]+)', content_disposition, re.IGNORECASE)
    if match:
        return sanitize_filename(match.group(1).strip())
    return None


async def descargar_documentos(url: str, notificar=None):
    msg = "🚀 Iniciando Playwright..."
    print(msg)
    if notificar:
        await notificar(msg)

    # === RUTA DE DESCARGAS (compatible Mac/Docker) ===
    DOWNLOAD_DIR = os.getenv("DOWNLOAD_DIR")
    base_dir = Path(DOWNLOAD_DIR).expanduser() if DOWNLOAD_DIR else Path(user_downloads_dir())
   
    ruta_descarga = Path(user_downloads_dir()) / "SIGED_DOCUMENTOS"
    os.makedirs(ruta_descarga, exist_ok=True)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=["--disable-dev-shm-usage"])
        context = await browser.new_context(accept_downloads=True)
        page = await context.new_page()

        msg = "🔄 Cargando la página principal..."
        print(msg)
        if notificar:
            await notificar(msg)

        await page.goto(url, timeout=90000, wait_until="load")

        msg = "✅ Página cargada con éxito"
        print(msg)
        if notificar:
            await notificar(msg)

        # (Opcional) conteo rápido de <a>
        total_a = await page.locator("a").count()
        print(f"[DEBUG] Total <a>: {total_a}")

        # === BUSCAR EN HREF Y EN ONCLICK ===
        candidate = page.locator('a[href*="apex.navigation.dialog"], a[onclick*="apex.navigation.dialog"]')
        n = await candidate.count()

        if n == 0:
            msg = "❌ No se encontraron enlaces de descarga (ni en href ni en onclick)."
            print(msg)
            if notificar:
                await notificar(msg)
            await browser.close()
            return

        msg = f"🔗 Se encontraron {n} documentos para descargar."
        print(msg)
        if notificar:
            await notificar(msg)

        base_url = "https://cgrweb.cgr.go.cr/apex/"

        for index in range(n):
            msg = f"📂 Abriendo documento {index + 1}..."
            print(msg)
            if notificar:
                await notificar(msg)

            link = candidate.nth(index)

            # Algunas veces abre nueva pestaña; otras, reutiliza la misma.
            scope_page = None
            try:
                async with context.expect_page(timeout=5000) as new_page_info:
                    await link.click()
                scope_page = await new_page_info.value
                await scope_page.wait_for_load_state("load")
                await scope_page.wait_for_timeout(1500)
            except Exception:
                # No abrió nueva pestaña: trabajar en la misma
                await link.click()
                await page.wait_for_load_state("networkidle")
                await page.wait_for_timeout(1500)
                scope_page = page

            # Buscar <embed> en la página "scope"
            embed_locator = scope_page.locator("embed")

            # Si no hay <embed>, a veces está dentro de un iframe
            if await embed_locator.count() == 0:
                # Probar iframes
                frames = scope_page.frames
                for fr in frames:
                    try:
                        emb = fr.locator("embed")
                        if await emb.count() > 0:
                            embed_locator = emb
                            break
                    except Exception:
                        continue

            if await embed_locator.count() > 0:
                # Tomar el primero
                file_url = await embed_locator.first.get_attribute("src")
                if not file_url:
                    msg = f"❌ No se pudo obtener 'src' del <embed> en documento {index+1}."
                    print(msg)
                    if notificar:
                        await notificar(msg)
                else:
                    # Normalizar URL
                    if not file_url.startswith("http"):
                        if file_url.startswith("/"):
                            full_url = base_url.rstrip("/") + file_url
                        else:
                            full_url = base_url.rstrip("/") + "/" + file_url
                    else:
                        full_url = file_url

                    msg = f"📄 Documento {index+1} encontrado: {full_url}"
                    print(msg)
                    if notificar:
                        await notificar(msg)

                    file_response = await scope_page.request.get(full_url)
                    file_name = await get_filename_from_headers(file_response)
                    if not file_name:
                        file_name = f"Documento_{index+1}.pdf"

                    file_content = await file_response.body()
                    file_path = ruta_descarga / file_name
                    with open(file_path, "wb") as f:
                        f.write(file_content)

                    msg = f"✅ Documento {index+1} descargado como: {file_name}"
                    print(msg)
                    if notificar:
                        await notificar(msg)
            else:
                msg = f"❌ No se encontró un <embed> en el documento {index+1} (ni en iframes)."
                print(msg)
                if notificar:
                    await notificar(msg)

            # Cerrar pestaña si se abrió nueva
            if scope_page is not page:
                await scope_page.close()

        await browser.close()

        msg = f"👋 Proceso completado. Archivos en: {ruta_descarga}"
        print(msg)
        if notificar:
            await notificar(msg)


# Para pruebas manuales desde consola
if __name__ == "__main__":
    test_url = "https://cgrweb.cgr.go.cr/apex/f?p=CORRESPONDENCIA:1:::::P1_CONSECUTIVO:A88C108C63FD77A3C0E96E1EE8FC6802"
    asyncio.run(descargar_documentos(test_url))