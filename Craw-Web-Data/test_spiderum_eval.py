import asyncio
from playwright.async_api import async_playwright
import json

async def test_api_eval():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context()
        page = await context.new_page()
        
        # Load the main site once to get cookies
        await page.goto("https://spiderum.com/")
        await asyncio.sleep(2) # Wait for cookies to settle
        
        slug = "Cha-toi-khong-xung-dang-duoc-yeu-thuong-RNNJNu"
        api_url = f"/api/v1/posts/getPostDetail?postSlug={slug}"
        
        # Fetch API inside the browser
        print(f"Fetching {api_url} via evaluate...")
        response_json = await page.evaluate(f"""
            async () => {{
                const res = await fetch("{api_url}");
                return await res.json();
            }}
        """)
        
        if 'post' in response_json:
            print(f"SUCCESS! Title: {response_json['post']['title']}")
        else:
            print(f"FAILED: {response_json}")
            
        await browser.close()

if __name__ == "__main__":
    asyncio.run(test_api_eval())
