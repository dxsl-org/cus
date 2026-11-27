import asyncio
from playwright.async_api import async_playwright

async def debug_dom():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        url = "https://spiderum.com/bai-dang/Cha-toi-khong-xung-dang-duoc-yeu-thuong-RNNJNu"
        await page.goto(url, wait_until="networkidle")
        
        # Look for the title
        title = await page.title()
        print(f"Page Title: {title}")
        
        # List all H1 and their classes
        h1s = await page.eval_on_selector_all("h1", "els => els.map(el => ({text: el.innerText, class: el.className}))")
        print(f"H1s: {h1s}")
        
        # Find candidate content divs (large text blocks)
        content_candidates = await page.evaluate("""() => {
            const divs = Array.from(document.querySelectorAll('div, article, section'));
            return divs
                .map(div => ({ 
                    tag: div.tagName, 
                    class: div.className, 
                    len: div.innerText.length,
                    html: div.innerHTML.substring(0, 100)
                }))
                .filter(d => d.len > 1000)
                .sort((a, b) => b.len - a.len)
                .slice(0, 5);
        }""")
        print(f"Content Candidates: {content_candidates}")
        
        await browser.close()

if __name__ == "__main__":
    asyncio.run(debug_dom())
