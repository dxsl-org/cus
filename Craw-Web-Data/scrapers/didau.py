import os
import json
import time
import asyncio
from playwright.async_api import async_playwright
from bs4 import BeautifulSoup
from markdownify import markdownify as md
from urllib.parse import urljoin
from scrapers.storage_utils import get_storage

def process(source, global_settings):
    asyncio.run(process_async(source, global_settings))

async def process_async(source, global_settings):
    storage = get_storage(source['id'])
    progress_file = source.get('progress_file', f"progress_{source['id']}.json")
    
    progress = {}
    if os.path.exists(progress_file):
        try:
            with open(progress_file, 'r', encoding='utf-8') as f:
                progress = json.load(f)
        except: pass
            
    if 'scraped_urls' not in progress: progress['scraped_urls'] = {}

    print(f"Starting Didau crawler (SPA + Playwright)")
    
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36"
        )
        page = await context.new_page()
        
        # Major categories
        categories = [
            "https://didau.com/chuyen-muc/doi-song",
            "https://didau.com/chuyen-muc/an-uong",
            "https://didau.com/chuyen-muc/tin-khuyen-mai",
            "https://didau.com/chuyen-muc/du-lich"
        ]
        
        try:
            for cat_url in categories:
                print(f"Crawling category: {cat_url}")
                await page.goto(cat_url, wait_until="networkidle", timeout=60000)
                
                # Programmatic "Load More" loop
                for i in range(15): # Click up to 15 times to load history
                    try:
                        view_more = page.locator(".btn-view-more")
                        if await view_more.is_visible():
                            print(f"  Clicking Load More ({i+1})")
                            await view_more.click()
                            await asyncio.sleep(2) # Wait for AJAX
                        else:
                            break
                    except:
                        break
                
                content = await page.content()
                soup = BeautifulSoup(content, 'html.parser')
                
                # In SPA, links might be in a different structure
                links = await page.eval_on_selector_all('a', 'elements => elements.map(el => el.href)')
                # Filter for links that look like articles (not category, login, etc)
                blacklist = ['/chuyen-muc/', '/login', '/signup', '/account', 'tel:', 'mailto:']
                relevant_links = []
                for l in links:
                    if l.startswith('https://didau.com/') and not any(b in l for b in blacklist):
                        if len(l.split('/')) > 3: # Has slug
                            relevant_links.append(l)
                
                relevant_links = list(set(relevant_links))
                
                print(f"  Found {len(relevant_links)} potential article links.")
                
                count = 0
                for url in relevant_links:
                    if url in progress['scraped_urls']: continue
                    
                    if await scrape_article(page, url, storage):
                        count += 1
                        progress['scraped_urls'][url] = time.time()
                        with open(progress_file, 'w', encoding='utf-8') as f:
                            json.dump(progress, f, indent=2)
                    
                    await asyncio.sleep(global_settings.get('request_delay', 1.0))
                
                print(f"  Scraped {count} articles from {cat_url}")
                
        except Exception as e:
            print(f"Error in Didau process: {e}")
        finally:
            await browser.close()

async def scrape_article(page, url, storage):
    try:
        await page.goto(url, wait_until="networkidle", timeout=60000)
        content = await page.content()
        soup = BeautifulSoup(content, 'html.parser')
        
        title_el = soup.find('h1')
        title = title_el.get_text(strip=True) if title_el else ""
        if not title: return False
        
        # Content usually in an article or specific div in SPAs
        content_el = soup.select_one('article, .post-content, .entry-content, .content-detail')
        if not content_el: return False
        
        # remove junk
        for junk in content_el.select('.adsbygoogle, .related-posts, script, style, .social-share'):
            junk.decompose()
            
        content_md = md(str(content_el), heading_style="ATX").strip()
        
        return storage.insert_article(
            url=url,
            title=title,
            content=content_md,
            metadata={"source": "didau.com"}
        )
    except: pass
    return False
