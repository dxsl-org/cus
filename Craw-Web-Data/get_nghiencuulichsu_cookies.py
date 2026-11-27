import asyncio
from playwright.async_api import async_playwright
import json

async def run():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        page = await context.new_page()
        
        print("Navigating to https://nghiencuulichsu.com...")
        try:
            # Wait for the network to be idle, which usually means the CF challenge is done
            await page.goto("https://nghiencuulichsu.com", wait_until="networkidle", timeout=60000)
            print("Page loaded.")
            
            # Check if we are still on the challenge page
            title = await page.title()
            print(f"Page Title: {title}")
            
            if "Just a moment" in title or "Checking your browser" in title:
                print("Stuck on challenge page. Trying to wait longer...")
                await asyncio.sleep(10)
                title = await page.title()
                print(f"New Title: {title}")

            # Get cookies
            cookies = await context.cookies()
            print(f"Retrieved {len(cookies)} cookies.")
            
            # Save cookies
            with open("nghiencuulichsu_cookies.json", "w") as f:
                json.dump(cookies, f)
            print("Cookies saved to nghiencuulichsu_cookies.json")
            
            # Check API with these cookies
            print("Testing API with cookies...")
            cookie_str = "; ".join([f"{c['name']}={c['value']}" for c in cookies])
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Cookie": cookie_str
            }
            
            # Test in the script using requests (simulated)
            import requests
            r = requests.get("https://nghiencuulichsu.com/wp-json/wp/v2/posts?per_page=1", headers=headers, timeout=15)
            print(f"API Test Status: {r.status_code}")
            if r.status_code == 200:
                print("SUCCESS: API accessed with cookies.")
                print(f"Total posts: {r.headers.get('X-WP-Total')}")
            else:
                print(f"API Test Failed. Snippet: {r.text[:200]}")
                
        except Exception as e:
            print(f"Error: {e}")
        finally:
            await browser.close()

asyncio.run(run())
