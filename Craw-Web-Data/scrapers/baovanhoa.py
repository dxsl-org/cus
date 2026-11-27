"""
Bao Van Hoa Scraper Plugin (Deep Scan Version)
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
    base_url = "https://baovanhoa.vn"
    sitemap_index_url = "https://baovanhoa.vn/sitemaps.xml"
    progress_file = source.get('progress_file', f"progress_{source.get('id')}.json")
    
    # Initialize SQLite Storage
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
    
    print(f"Starting Deep Sitemap Scan for {source['name']} at {sitemap_index_url}")
    
    try:
        r = requests.get(sitemap_index_url, headers=headers, timeout=30)
        soup = BeautifulSoup(r.text, 'xml')
        
        # Get all sub-sitemaps (monthly/daily)
        sitemaps = [loc.text for loc in soup.find_all('loc')]
        print(f"Found {len(sitemaps)} sub-sitemaps in index.")
        
        # Traverse ALL sitemaps for deep scan
        for sm in reversed(sitemaps):
            if sm in progress.get('processed_sitemaps', []):
                # We can skip if it's an old one already fully processed, 
                # but for a "re-scan" we might want to check URLs again.
                # However, usually we just skip to save time.
                continue
                
            print(f"Processing sub-sitemap: {sm}")
            try:
                sr = requests.get(sm, headers=headers, timeout=30)
                ssoup = BeautifulSoup(sr.text, 'xml')
                urls = [loc.text for loc in ssoup.find_all('loc')]
                
                for url in urls:
                    if url in progress['scraped_urls']:
                        continue
                    
                    # Filter for article URLs
                    if '.html' in url and '/content/' not in url: # Content pages usually have more data than dynamic redirects
                         scrape_article(url, storage, progress, headers, global_settings)
                         
                         # Frequent save
                         with open(progress_file, 'w', encoding='utf-8') as f:
                             json.dump(progress, f, indent=2)

                if 'processed_sitemaps' not in progress:
                    progress['processed_sitemaps'] = []
                progress['processed_sitemaps'].append(sm)
                
            except Exception as e:
                print(f"Error processing sitemap {sm}: {e}")
                
    except Exception as e:
        print(f"Error in deep sitemap scan: {e}")

def scrape_article(url, storage, progress, headers, global_settings):
    try:
        # print(f"Scraping: {url}")
        resp = requests.get(url, headers=headers, timeout=30)
        if resp.status_code != 200:
            return
            
        soup = BeautifulSoup(resp.text, 'html.parser')
        
        # Specific selectors for Báo Văn Hóa
        title_el = soup.select_one('h1.detail__title, .detail-title, h1')
        if not title_el:
            return
        title = title_el.get_text(strip=True)
        
        sapo_el = soup.select_one('h2.detail__summary, .detail-sapo, .sapo')
        sapo = sapo_el.get_text(strip=True) if sapo_el else ""
        
        content_el = soup.select_one('div#abody, .detail-content, .content')
        if not content_el:
            return

        # Meta extraction
        author = ""
        author_el = soup.select_one('.detail__author, .author')
        if author_el:
            author = author_el.get_text(strip=True)
            
        published_date = ""
        date_el = soup.select_one('time, .detail__time time')
        if date_el:
            published_date = date_el.get_text(strip=True)
            
        tags = []
        tag_els = soup.select('a.tag-item, .tags a')
        for t in tag_els:
            tags.append(t.get_text(strip=True))

        # Convert to markdown
        content_html = str(content_el)
        content_md = md(content_html, heading_style="ATX")
        
        if sapo:
            content_md = f"**{sapo}**\n\n{content_md}"

        # Insert into SQLite
        success = storage.insert_article(
            url=url,
            title=title,
            content=content_md,
            author=author,
            tags=tags,
            metadata={'published_date': published_date}
        )
        
        if success:
            progress['scraped_urls'][url] = time.time()
            print(f"SUCCESS: {title[:50]}...")
        
        time.sleep(global_settings.get('request_delay', 0.5)) # Slightly faster for deep scan if allowed
        
    except Exception as e:
        print(f"Failed to scrape {url}: {e}")
