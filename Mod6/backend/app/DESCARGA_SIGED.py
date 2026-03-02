import sys
import asyncio
import os
import re
import unicodedata
from urllib.parse import unquote
from pathlib import Path
from platformdirs import user_downloads_dir
from playwright.async_api import async_playwright

def sanitize_filename(filename):
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

async def descargar_documentos(url, notificar=None):
    msg = "🚀 Iniciando Playwright..."
    print(msg)
    if notificar: await notificar(msg)

    # Crear carpeta SIGED_DOCUMENTOS dentro de la carpeta de Descargas del usuario
    ruta_descarga = Path(user_downloads_dir()) / "SIGED_DOCUMENTOS"
    os.makedirs(ruta_descarga, exist_ok=True)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context(accept_downloads=True)
        page = await context.new_page()

        msg = "🔄 Cargando la página principal..."
        print(msg)
        if notificar: await notificar(msg)

        await page.goto(url, timeout=90000)

        msg = "✅ Página cargada con éxito"
        print(msg)
        if notificar: await notificar(msg)

        links = await page.locator("a").all()
        download_links = [link for link in links if "apex.navigation.dialog" in str(await link.get_attribute("href"))]

        if not download_links:
            msg = "❌ No se encontraron enlaces de descarga."
            print(msg)
            if notificar: await notificar(msg)
            await browser.close()
            return

        msg = f"🔗 Se encontraron {len(download_links)} documentos para descargar."
        print(msg)
        if notificar: await notificar(msg)

        base_url = "https://cgrweb.cgr.go.cr/apex/"

        for index, link in enumerate(download_links):
            msg = f"📂 Abriendo documento {index + 1}..."
            print(msg)
            if notificar: await notificar(msg)

            async with context.expect_page() as new_page_info:
                await link.click()
            new_page = await new_page_info.value
            await new_page.wait_for_load_state("load")
            await new_page.wait_for_timeout(3000)

            embed_element = new_page.locator("embed")
            if await embed_element.count() > 0:
                file_url = await embed_element.get_attribute("src")
                full_url = base_url + file_url if not file_url.startswith("http") else file_url

                msg = f"📄 Documento {index+1} encontrado: {full_url}"
                print(msg)
                if notificar: await notificar(msg)

                file_response = await new_page.request.get(full_url)
                file_name = await get_filename_from_headers(file_response)
                if not file_name:
                    file_name = f"Documento_{index+1}.pdf"

                file_content = await file_response.body()
                file_path = ruta_descarga / file_name
                with open(file_path, "wb") as f:
                    f.write(file_content)

                msg = f"✅ Documento {index+1} descargado como: {file_name}"
                print(msg)
                if notificar: await notificar(msg)
            else:
                msg = f"❌ No se encontró un documento en el documento {index+1}."
                print(msg)
                if notificar: await notificar(msg)

            await new_page.close()

        await browser.close()

        msg = "👋 Proceso completado."
        print(msg)
        if notificar: await notificar(msg)

# Para pruebas manuales desde consola
if __name__ == "__main__":
    test_url = "https://cgrweb.cgr.go.cr/apex/f?p=CORRESPONDENCIA:1:::::P1_CONSECUTIVO:A88C108C63FD77A3C0E96E1EE8FC6802"
    asyncio.run(descargar_documentos(test_url))