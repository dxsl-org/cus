"""
Thanh Nien Sitemap Scraper Plugin (SQLite Version)
"""
import os
import re
import time
import requests
from bs4 import BeautifulSoup
from markdownify import markdownify as md
from scrapers.storage_utils import get_storage

def process(source, global_settings):
    storage = get_storage(source['id'])
    progress_file = source.get('progress_file', f"progress_{source['id']}.json")
    
    progress = {}
    if os.path.exists(progress_file):
        try:
            with open(progress_file, 'r', encoding='utf-8') as f:
                progress = json.load(f)
        except: pass
            
    if 'scraped_urls' not in progress: progress['scraped_urls'] = {}

    headers = {"User-Agent": global_settings.get('user_agent', "Mozilla/5.0")}
    print(f"Starting Thanh Nien Sitemap Crawler...")
    
    sitemap_url = "https://thanhnien.vn/sitemap.xml"
    try:
        r = requests.get(sitemap_url, headers=headers, timeout=30)
        # Parse sitemap index
        sub_sitemaps = re.findall(r'<loc>(.*?sitemap-article.*?\.xml)</loc>', r.text)
        if not sub_sitemaps:
            # Fallback to news sitemap
            sub_sitemaps = ["https://thanhnien.vn/sitemap-news.xml"]
            
        print(f"Found {len(sub_sitemaps)} sitemaps.")
        
        for sm in reversed(sub_sitemaps):
            sr = requests.get(sm, headers=headers, timeout=30)
            urls = re.findall(r'<loc>(.*?\.html)</loc>', sr.text)
            print(f"  Processing {sm}: Found {len(urls)} URLs")
            
            for url in urls:
                if url in progress['scraped_urls']: continue
                
                scrape_article(url, storage, progress, headers, global_settings)
                
                if len(progress['scraped_urls']) % 10 == 0:
                    with open(progress_file, 'w', encoding='utf-8') as f:
                        json.dump(progress, f, indent=2)
                        
    except Exception as e:
        print(f"Error in Thanh Nien process: {e}")

def scrape_article(url, storage, progress, headers, global_settings):
    try:
        resp = requests.get(url, headers=headers, timeout=30)
        if resp.status_code != 200:
            return
            
        soup = BeautifulSoup(resp.text, 'html.parser')
        
        title_el = soup.select_one('h1.detail-title, h1')
        title = title_el.text.strip() if title_el else "No Title"
        
        content_el = soup.select_one('div.detail-content, .cms-body, .detail-content-body')
        author_el = soup.select_one('.detail-author, .details__author')
        author = author_el.text.strip() if author_el else "Unknown"
        
        if not content_el:
            return
            
        # Remove junk
        for junk in content_el.select('.detail-content-relate, .adsbygoogle, script, style'):
            junk.decompose()
            
        content_md = md(str(content_el), heading_style="ATX")
        
        # Insert into SQLite
        success = storage.insert_article(
            url=url,
            title=title,
            author=author,
            content=content_md,
            metadata={"source": "ThanhNien"}
        )
        if success:
            progress['scraped_urls'][url] = time.time()
            
    except Exception as e:
        print(f"Error scraping article {url}: {e}")
