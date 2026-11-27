from playwright.sync_api import sync_playwright
import time
import sys

def debug_thread():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
        
        url = "https://voz.vn/t/lam-sao-de-lam-cho-cac-cong-ty-high-traffic.1213955/"
        print(f"Opening thread: {url}")
        page.goto(url, wait_until="domcontentloaded")
        time.sleep(10) # wait for CF
        
        print("Page Title:", page.title())
        page.screenshot(path="e:/Data/debug_voz_thread.png")
        
        with open("e:/Data/debug_voz_thread.html", "w", encoding="utf-8") as f:
            f.write(page.content())
            
        posts = page.query_selector_all('.message-inner')
        print(f"Found {len(posts)} .message-inner elements.")
        
        # Test specific Xenforo 2 selectors
        posts2 = page.query_selector_all('article.message')
        print(f"Found {len(posts2)} article.message elements.")
        
        browser.close()

if __name__ == "__main__":
    debug_thread()
