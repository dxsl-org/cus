import asyncio
from playwright.async_api import async_playwright
import json

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        # Use a real browser context
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        page = await context.new_page()
        
        # Monitor network
        api_headers = {}
        async def handle_request(request):
            if "getPostDetail" in request.url:
                nonlocal api_headers
                api_headers = request.headers
        
        page.on("request", handle_request)
        
        # Visit a sample article
        url = "https://spiderum.com/bai-dang/Cha-toi-khong-xung-dang-duoc-yeu-thuong-RNNJNu"
        await page.goto(url, wait_until="networkidle")
        
        # Get cookies
        cookies = await context.cookies()
        
        result = {
            "headers": api_headers,
            "cookies": {c['name']: c['value'] for c in cookies},
            "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }
        
        with open("C:/tmp/spiderum_credentials.json", "w", encoding='utf-8') as f:
            json.dump(result, f, indent=2)
            
        print("Credentials saved to C:/tmp/spiderum_credentials.json")
        await browser.close()

if __name__ == "__main__":
    asyncio.run(main())
