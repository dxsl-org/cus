import asyncio
from playwright.async_api import async_playwright

async def snap():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            viewport={'width': 1280, 'height': 720}
        )
        page = await context.new_page()
        # Stealth init
        await page.add_init_script("Object.defineProperty(navigator, 'webdriver', { get: () => undefined });")
        
        url = 'https://spiderum.com/bai-dang/Cha-toi-khong-xung-dang-duoc-yeu-thuong-RNNJNu'
        print(f"Opening {url}...")
        await page.goto(url, wait_until='networkidle')
        await asyncio.sleep(5)
        
        path = 'C:/tmp/spiderum_snap.png'
        await page.screenshot(path=path)
        print(f"Screenshot saved to {path}")
        await browser.close()

if __name__ == "__main__":
    asyncio.run(snap())
