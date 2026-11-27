import asyncio
from playwright.async_api import async_playwright

async def debug_stealth():
    async with async_playwright() as p:
        # Use a more realistic user agent and context
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            viewport={"width": 1280, "height": 720}
        )
        
        page = await context.new_page()
        
        # Stealth: Remove webdriver property
        await page.add_init_script("""() => {
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
        }""")
        
        url = "https://spiderum.com/bai-dang/Cha-toi-khong-xung-dang-duoc-yeu-thuong-RNNJNu"
        print(f"Visiting {url} with stealth...")
        
        try:
            resp = await page.goto(url, wait_until="networkidle", timeout=60000)
            print(f"Status Code: {resp.status}")
            
            # Wait a bit more for SPA to load
            await asyncio.sleep(5)
            
            title = await page.title()
            print(f"Page Title: {title}")
            
            # Check for H1 again
            h1s = await page.eval_on_selector_all("h1", "els => els.map(el => el.innerText)")
            print(f"H1s: {h1s}")
            
            # Look for content in common selectors
            content = await page.evaluate("""() => {
                const selectors = ['.post-content', '.post-detail__content', '.article-content', 'article', '.content'];
                for (let s of selectors) {
                    const el = document.querySelector(s);
                    if (el && el.innerText.length > 500) {
                        return { selector: s, length: el.innerText.length, snippet: el.innerText.substring(0, 100) };
                    }
                }
                return null;
            }""")
            print(f"Content Check: {content}")
            
        except Exception as e:
            print(f"Error: {e}")
            
        await browser.close()

if __name__ == "__main__":
    asyncio.run(debug_stealth())
