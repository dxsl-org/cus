import os
import json
import time
import requests
from bs4 import BeautifulSoup
from markdownify import markdownify as md
from scrapers.storage_utils import get_storage

def process(source, global_settings):
    storage = get_storage(source['id'])
    progress_file = source.get('progress_file', f"progress_{source.get('id')}.json")
    
    progress = {}
    if os.path.exists(progress_file):
        try:
            with open(progress_file, 'r', encoding='utf-8') as f:
                progress = json.load(f)
        except:
            progress = {}
            
    if 'scraped_urls' not in progress:
        progress['scraped_urls'] = {}

    sitemap_url = "https://nguoidothi.net.vn/sitemap.xml"
    print(f"Fetching sitemap from {sitemap_url}")
    
    try:
        r = requests.get(sitemap_url, timeout=30)
        if r.status_code != 200:
            print(f"Failed to fetch sitemap: {r.status_code}")
            return
            
        soup = BeautifulSoup(r.text, 'xml')
        urls = [loc.text for loc in soup.find_all('loc') if '.html' in loc.text]
        
        print(f"Found {len(urls)} article URLs in sitemap.")
        
        # Process in reverse (newest usually first in sitemaps, but let's see)
        # Actually, let's just go through them.
        count = 0
        for url in reversed(urls):
            if url in progress['scraped_urls']:
                continue
            
            success = scrape_article(url, storage, progress, global_settings)
            if success:
                count += 1
                if count % 10 == 0:
                    print(f"Progress: {count} articles scraped this session.")
                    with open(progress_file, 'w', encoding='utf-8') as f:
                        json.dump(progress, f, indent=2)
                        
    except Exception as e:
        print(f"Error in Người Đô Thị process: {e}")
        
    # Final save
    with open(progress_file, 'w', encoding='utf-8') as f:
        json.dump(progress, f, indent=2)

def scrape_article(url, storage, progress, global_settings):
    try:
        headers = {
            "User-Agent": global_settings.get('user_agent', 'Mozilla/5.0')
        }
        resp = requests.get(url, headers=headers, timeout=20)
        if resp.status_code != 200:
            return False
            
        soup = BeautifulSoup(resp.text, 'html.parser')
        
        title_el = soup.select_one('h1')
        title = title_el.get_text(strip=True) if title_el else ""
        
        content_el = soup.select_one('#dvContent')
        if not content_el:
            return False
            
        # Clean up some common noise if any
        # ... none obvious yet
        
        content_md = md(str(content_el), heading_style="ATX")
        
        # Metadata
        date_el = soup.select_one('.articleDetail span')
        date_str = date_el.get_text(strip=True) if date_el else ""
        
        author = "Unknown"
        author_el = content_el.find_all(['p', 'div'], style=lambda x: x and 'text-align: right' in x)
        if not author_el:
             # Try strong at the end
             strongs = content_el.find_all('strong')
             if strongs:
                 author = strongs[-1].get_text(strip=True)
        else:
            author = author_el[-1].get_text(strip=True)

        category = ""
        cat_el = soup.select_one('.dvTitleZone h2 a')
        if cat_el:
            category = cat_el.get_text(strip=True)

        tags = [a.get_text(strip=True) for a in soup.select('.itemTags font a')]

        success = storage.insert_article(
            url=url,
            title=title,
            author=author,
            content=content_md,
            category=category,
            tags=tags,
            metadata={"publish_date": date_str}
        )
        
        if success:
            progress['scraped_urls'][url] = time.time()
            # print(f"Scraped: {title}")
            return True
            
        time.sleep(global_settings.get('request_delay', 1.0))
        
    except Exception as e:
        print(f"Failed to scrape {url}: {e}")
    return False
