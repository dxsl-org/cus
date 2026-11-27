"""
Generic Sitemap Scraper Plugin for SGGP, PLO, etc.
"""
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
    # Standard XML sitemap or RSS feed
    sitemap_url = source.get('sitemap_url', f"{base_url}/sitemap.xml")
    progress_file = source.get('progress_file', f"progress_{source.get('id')}.json")
    
    # Initialize SQLite Storage
    storage = get_storage(source['id'])
    
    progress = {}
    if os.path.exists(progress_file):
        with open(progress_file, 'r', encoding='utf-8') as f:
            progress = json.load(f)
            
    headers = {
        "User-Agent": global_settings.get('user_agent', "Mozilla/5.0")
    }
    
    print(f"Starting sitemap crawler for {source['name']} (Storage: SQLite) at {sitemap_url}")
    
    try:
        r = requests.get(sitemap_url, headers=headers, timeout=30)
        soup = BeautifulSoup(r.text, 'xml')
        
        # Handle sitemap index or direct sitemap
        sitemaps = [loc.text for loc in soup.find_all('loc') if loc.text.endswith('.xml')]
        urls = [loc.text for loc in soup.find_all('loc') if not loc.text.endswith('.xml')]
        
        if sitemaps:
            print(f"Found {len(sitemaps)} sub-sitemaps")
            # Usually only scan the most recent one for news
            for sm in reversed(sitemaps[-2:]):
                sr = requests.get(sm, headers=headers, timeout=30)
                ssoup = BeautifulSoup(sr.text, 'xml')
                urls.extend([loc.text for loc in ssoup.find_all('loc')])
        
        unique_urls = list(set(urls))
        print(f"Found {len(unique_urls)} total URLs in sitemaps")
        
        for url in unique_urls:
            if url in progress.get('scraped_urls', {}):
                continue
                
            # Filter for articles (site specific patterns would be better, but generic works for now)
            if not re.search(r'\.html$|/\d+|^https?://[^/]+/\w', url):
                 continue

            scrape_article(source, url, storage, progress, headers, global_settings)
            
            with open(progress_file, 'w', encoding='utf-8') as f:
                json.dump(progress, f, indent=2)
                
    except Exception as e:
        print(f"Error in sitemap process for {source['name']}: {e}")

def scrape_article(source, url, storage, progress, headers, global_settings):
    try:
        print(f"Scraping: {url}")
        resp = requests.get(url, headers=headers, timeout=30)
        if resp.status_code != 200:
            return
            
        soup = BeautifulSoup(resp.text, 'html.parser')
        
        # Generic news selectors
        title = soup.find('h1').get_text(strip=True) if soup.find('h1') else "No Title"
        
        # Try common content selectors
        content_el = soup.select_one('.article-content, .detail-content, .content-detail, .entry-content, .post-content, #main-detail')
        
        if not content_el:
            # Fallback to a broader search if generic fails
            content_el = soup.find('article')
            
        if not content_el:
            return
            
        content_md = md(str(content_el), heading_style="ATX")
        
        # Insert into SQLite
        success = storage.insert_article(
            url=url,
            title=title,
            content=content_md
        )
        
        if success:
            if 'scraped_urls' not in progress: progress['scraped_urls'] = {}
            progress['scraped_urls'][url] = time.time()
        
        time.sleep(global_settings.get('request_delay', 1))
        
    except Exception as e:
        print(f"Failed to scrape {url}: {e}")
