import os
import re
import json
import time
import requests
from bs4 import BeautifulSoup
from markdownify import markdownify as md
from scrapers.storage_utils import get_storage

def process(source, global_settings):
    base_url = source.get('url').rstrip('/')
    sitemap_url = "https://tgs.vn/sitemap.xml"
    progress_file = source.get('progress_file', f"progress_{source.get('id')}.json")
    storage = get_storage(source['id'])
    
    progress = {}
    if os.path.exists(progress_file):
        try:
            with open(progress_file, 'r', encoding='utf-8') as f:
                progress = json.load(f)
        except:
            progress = {}
            
    if 'scraped_urls' not in progress:
        progress['scraped_urls'] = {}
        
    headers = {
        "User-Agent": global_settings.get('user_agent', "Mozilla/5.0")
    }
    
    print(f"Starting TGS.vn Deep Scan from {sitemap_url}")
    
    try:
        r = requests.get(sitemap_url, headers=headers, timeout=30)
        soup = BeautifulSoup(r.text, 'xml')
        
        # Find all monthly post sitemaps
        sitemaps = [loc.text for loc in soup.find_all('loc') if 'sitemap-posttype-post' in loc.text and loc.text.endswith('.xml')]
        
        print(f"Found {len(sitemaps)} monthly archives.")
        
        # Process sitemaps in reverse (newest first)
        for sm_url in reversed(sitemaps):
            print(f"Processing sitemap: {sm_url}")
            sr = requests.get(sm_url, headers=headers, timeout=30)
            ssoup = BeautifulSoup(sr.text, 'xml')
            urls = [loc.text for loc in ssoup.find_all('loc')]
            
            for url in urls:
                if url in progress['scraped_urls']:
                    continue
                
                scrape_article(url, storage, progress, headers, global_settings)
                
                # Periodic save
                if len(progress['scraped_urls']) % 10 == 0:
                    with open(progress_file, 'w', encoding='utf-8') as f:
                        json.dump(progress, f, indent=2)
                        
    except Exception as e:
        print(f"Error in TGS.vn process: {e}")
        
    # Final save
    with open(progress_file, 'w', encoding='utf-8') as f:
        json.dump(progress, f, indent=2)

def scrape_article(url, storage, progress, headers, global_settings):
    try:
        resp = requests.get(url, headers=headers, timeout=30)
        if resp.status_code != 200:
            return
            
        soup = BeautifulSoup(resp.text, 'html.parser')
        
        title = soup.find('h1').get_text(strip=True) if soup.find('h1') else "No Title"
        
        # Main content is in div#editor
        content_el = soup.find(id='editor')
        if not content_el:
            # Fallback
            content_el = soup.select_one('.article-content, .td-post-content')
            
        if not content_el:
            print(f"Skipping {url}: No content element found.")
            return

        # Metadata from meta tags or specific elements
        author = "Unknown"
        author_el = soup.select_one('.meta-author-date .padding-5px-left a, .meta-author-date a')
        if author_el:
            author = author_el.get_text(strip=True)
        else:
            author_meta = soup.find('meta', property='article:author')
            if author_meta:
                author = author_meta.get('content')
                # If it's a FB URL, try to extract a name-like part or keep as is if too complex
                if 'facebook.com' in author:
                    author = author.rstrip('/').split('/')[-1]

        date = ""
        date_meta = soup.find('meta', property='article:published_time')
        if date_meta:
            date = date_meta.get('content')
        else:
            date_el = soup.select_one('.timeago')
            if date_el:
                date = date_el.get_text(strip=True)

        category = ""
        cat_el = soup.select_one('.catelogy a')
        if cat_el:
            category = cat_el.get_text(strip=True)

        tags = [a.get_text(strip=True) for a in soup.select('.post-tags a')]

        content_md = md(str(content_el), heading_style="ATX")
        
        success = storage.insert_article(
            url=url,
            title=title,
            author=author,
            content=content_md,
            category=category,
            tags=tags,
            metadata={"published_time": date}
        )
        
        if success:
            progress['scraped_urls'][url] = time.time()
            print(f"SUCCESS: {title[:50]}...")
        
        time.sleep(global_settings.get('request_delay', 1.0))
        
    except Exception as e:
        print(f"Failed to scrape {url}: {e}")
