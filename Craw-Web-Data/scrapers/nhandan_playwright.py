"""
Nhan Dan Playwright Scraper Plugin
Handles Cloudflare and JS-heavy content for nhandan.vn
"""
import os
import re
import json
import time
import asyncio
from playwright.async_api import async_playwright
import bs4
from markdownify import markdownify as md

from scrapers.storage_utils import get_storage

def process(source, global_settings):
    asyncio.run(async_process(source, global_settings))

async def async_process(source, global_settings):
    base_url = "https://nhandan.vn"
    progress_file = source.get('progress_file', 'progress_nhandan.json')
    
    # Initialize SQLite Storage
    storage = get_storage(source['id'])
    
    progress = {}
    if os.path.exists(progress_file):
        with open(progress_file, 'r', encoding='utf-8') as f:
            progress = json.load(f)
            
    print(f"Starting Nhan Dan Playwright crawler (Storage: SQLite)...")
    
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent=global_settings.get('user_agent', "Mozilla/5.0")
        )
        page = await context.new_page()
        
        try:
            # Step 1: Discover articles from homepage or category
            await page.goto(base_url, timeout=60000, wait_until="domcontentloaded")
            await page.wait_for_timeout(5000) # Wait for Cloudflare
            
            # Extract links
            links = await page.evaluate("""() => {
                return Array.from(document.querySelectorAll('a'))
                    .map(a => a.href)
                    .filter(href => href.includes('nhandan.vn') && href.length > 40);
            }""")
            
            unique_links = list(set(links))
            print(f"Found {len(unique_links)} potential article links")
            
            for url in unique_links:
                if url in progress.get('scraped_urls', {}):
                    continue
                
                await scrape_article(page, url, storage, progress, global_settings)
                
                # Save progress
                with open(progress_file, 'w', encoding='utf-8') as f:
                    json.dump(progress, f, indent=2)
                    
        except Exception as e:
            print(f"Error in Nhan Dan process: {e}")
        finally:
            await browser.close()

async def scrape_article(page, url, storage, progress, global_settings):
    try:
        print(f"Scraping: {url}")
        await page.goto(url, timeout=60000, wait_until="networkidle")
        await page.wait_for_timeout(2000)
        
        html = await page.content()
        soup = bs4.BeautifulSoup(html, 'html.parser')
        
        title_elem = soup.find('h1')
        if not title_elem:
            return
            
        title = title_elem.get_text(strip=True)
        sapo = soup.select_one('.article__sapo, .box-sapo, .detail-sapo, h2').get_text(strip=True) if soup.select_one('.article__sapo, .box-sapo, .detail-sapo, h2') else ""
        content_el = soup.select_one('.detail-content-body, .article__body, .article__main-content, .content-detail')
        
        if not content_el:
            return
            
        content_md = md(str(content_el), heading_style="ATX")
        if sapo:
            content_md = f"> {sapo}\n\n" + content_md
            
        # Insert into SQLite
        success = storage.insert_article(
            url=url,
            title=title,
            content=content_md,
            metadata={"sapo": sapo}
        )
        
        if success:
            if 'scraped_urls' not in progress: progress['scraped_urls'] = {}
            progress['scraped_urls'][url] = time.time()
        
        await asyncio.sleep(global_settings.get('request_delay', 1))
        
    except Exception as e:
        print(f"Failed to scrape {url}: {e}")
