import asyncio, os
from playwright.async_api import async_playwright

DOWNLOAD_DIR = "/downloads"

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=["--disable-dev-shm-usage","--no-sandbox"]
        )
        context = await browser.new_context()
        page = await context.new_page()
        await page.goto("https://example.com")
        os.makedirs(DOWNLOAD_DIR, exist_ok=True)
        await page.screenshot(path=f"{DOWNLOAD_DIR}/ok.png", full_page=True)
        await browser.close()

asyncio.run(main())
