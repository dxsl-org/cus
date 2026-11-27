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

    print(f"Starting VNPhoto crawler (vBulletin + Playwright)")
    
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        # Use a persistent context or at least a realistic user-agent
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36"
        )
        page = await context.new_page()
        
        # Categories
        categories = [
            "http://www.vnphoto.net/forums/forumdisplay.php?f=2", # Tin tức - Sự kiện
            "http://www.vnphoto.net/forums/forumdisplay.php?f=4", # Mua bán
            "http://www.vnphoto.net/forums/forumdisplay.php?f=11", # Ảnh chụp macro, tĩnh vật, sản phẩm
            "http://www.vnphoto.net/forums/forumdisplay.php?f=12", # Ảnh phượt, ảnh du lịch, ảnh phố
            "http://www.vnphoto.net/forums/forumdisplay.php?f=16", # Trao đổi về các loại máy ảnh và thiết bị chụp ảnh
            "http://www.vnphoto.net/forums/forumdisplay.php?f=17", # Thử nghiệm thiết bị
            "http://www.vnphoto.net/forums/forumdisplay.php?f=26", # Kỹ thuật nhiếp ảnh
            "http://www.vnphoto.net/forums/forumdisplay.php?f=50", # Chia sẻ kinh nghiệm & Những vấn đề có liên quan
            "http://www.vnphoto.net/forums/forumdisplay.php?f=51"  # Máy ảnh số (Digital)
        ]
        
        try:
            for cat_url in categories:
                print(f"Crawling category: {cat_url}")
                for p_num in range(1, 101): # Expanded to 100 pages per category
                    p_suffix = f"&page={p_num}" if p_num > 1 else ""
                    page_url = f"{cat_url}{p_suffix}"
                    
                    await page.goto(page_url, wait_until="domcontentloaded", timeout=60000)
                    # Small sleep for dynamic content
                    await asyncio.sleep(2)
                    
                    content = await page.content()
                    soup = BeautifulSoup(content, 'html.parser')
                    
                    # vBulletin thread links usually in h3.threadtitle a or similar
                    links = soup.select('h3.threadtitle a, a[id^="thread_title_"]')
                    if not links: break
                    
                    thread_urls = []
                    for link in links:
                        href = link.get('href')
                        if href and 'showthread.php' in href:
                            thread_urls.append(urljoin(cat_url, href))
                            
                    thread_urls = list(set(thread_urls))
                    
                    count = 0
                    for url in thread_urls:
                        clean_url = url.split('&s=')[0].split('#')[0]
                        if clean_url in progress['scraped_urls']: continue
                        
                        success = await scrape_thread(page, clean_url, storage)
                        if success:
                            count += 1
                            progress['scraped_urls'][clean_url] = time.time()
                            with open(progress_file, 'w', encoding='utf-8') as f:
                                json.dump(progress, f, indent=2)
                        
                        await asyncio.sleep(global_settings.get('request_delay', 1.0))
                        
                    print(f"  Scraped {count} threads from page {p_num}")
                    
        except Exception as e:
            print(f"Error in VNPhoto process: {e}")
        finally:
            await browser.close()

async def scrape_thread(page, url, storage):
    try:
        current_thread_url = url
        thread_content = ""
        thread_title = ""
        page_count = 0
        max_thread_pages = 20 # Limit to 20 pages per thread to keep article size manageable but comprehensive
        
        while current_thread_url and page_count < max_thread_pages:
            await page.goto(current_thread_url, wait_until="domcontentloaded", timeout=60000)
            await asyncio.sleep(1)
            
            content = await page.content()
            soup = BeautifulSoup(content, 'html.parser')
            
            if not thread_title:
                title_el = soup.select_one('.threadtitle, h1, .p-title-value')
                thread_title = title_el.get_text(strip=True) if title_el else ""
            
            if not thread_title: break
            
            posts = soup.select('li.postcontainer')
            if not posts: break
            
            for idx, post in enumerate(posts):
                author_el = post.select_one('.username')
                author = author_el.get_text(strip=True) if author_el else "Unknown"
                
                body_el = post.select_one('.postbody')
                if not body_el: continue
                
                # Remove quotes
                for quote in body_el.select('div.quote_container'):
                    quote.decompose()
                    
                body_md = md(str(body_el), heading_style="ATX").strip()
                
                if page_count == 0 and idx == 0:
                    thread_content += f"**{author}** hỏi/chia sẻ:\n\n{body_md}\n\n"
                else:
                    thread_content += f"**{author}** trả lời:\n\n{body_md}\n\n"
                
                thread_content += "---\n"
            
            # Check for next page
            next_page_el = soup.select_one('a[rel="next"]')
            if next_page_el and next_page_el.get('href'):
                current_thread_url = urljoin(current_thread_url, next_page_el.get('href'))
                page_count += 1
                # Small sleep before next page of the same thread
                await asyncio.sleep(0.5)
            else:
                current_thread_url = None
                
        if not thread_title or not thread_content: return False
        
        return storage.insert_article(
            url=url,
            title=thread_title,
            content=thread_content,
            metadata={"source": "vnphoto.net", "total_pages": page_count + 1}
        )
    except Exception as e:
        print(f"    Failed to scrape thread {url}: {e}")
    return False
