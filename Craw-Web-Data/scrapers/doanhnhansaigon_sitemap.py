"""
Doanh Nhân Sài Gòn Scraper Plugin - Comprehensive Sitemap Crawler
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
    base_url = source.get('url', "https://doanhnhansaigon.vn").rstrip('/')
    sitemap_index_url = f"{base_url}/sitemap.xml"
    progress_file = source.get('progress_file', f"progress_{source['id']}.json")
    
    # Initialize SQLite Storage
    storage = get_storage(source['id'])
    
    progress = {}
    if os.path.exists(progress_file):
        with open(progress_file, 'r', encoding='utf-8') as f:
            try:
                progress = json.load(f)
            except:
                progress = {}
            
    headers = {
        "User-Agent": global_settings.get('user_agent', "Mozilla/5.0")
    }
    
    print(f"Starting Doanh Nhân Sài Gòn comprehensive crawler (Storage: SQLite)...")
    
    session = requests.Session()
    session.headers.update(headers)
    
    try:
        print(f"Fetching sitemap index: {sitemap_index_url}")
        resp = session.get(sitemap_index_url, timeout=30)
        if resp.status_code != 200:
            print(f"Failed to fetch sitemap index: {resp.status_code}")
            return
            
        # Parse sitemap index
        # Use regex to find <loc> to avoid namespace issues
        sub_sitemaps = re.findall(r'<loc>(https?://doanhnhansaigon\.vn/sitemap-article-.*?\.xml)</loc>', resp.text)
        
        print(f"Found {len(sub_sitemaps)} sub-sitemaps.")
        
        # Process in reverse to get newest content first (optional, but usually better for news)
        for s_url in reversed(sub_sitemaps):
            if s_url in progress.get('completed_sitemaps', []):
                continue
                
            print(f"Processing sub-sitemap: {s_url}")
            try:
                s_resp = session.get(s_url, timeout=30)
                if s_resp.status_code == 200:
                    urls = re.findall(r'<loc>(.*?)</loc>', s_resp.text)
                    print(f"  Found {len(urls)} URLs in {s_url}")
                    
                    for url in urls:
                        if url in progress.get('scraped_urls', {}):
                            continue
                            
                        scrape_page(url, storage, progress, session, global_settings)
                        
                        # Save progress frequently
                        with open(progress_file, 'w', encoding='utf-8') as f:
                            json.dump(progress, f, indent=2)
                    
                    # Mark sitemap as completed
                    if 'completed_sitemaps' not in progress: progress['completed_sitemaps'] = []
                    progress['completed_sitemaps'].append(s_url)
                else:
                    print(f"  Failed to fetch sub-sitemap {s_url}: {s_resp.status_code}")
            except Exception as e:
                print(f"  Error processing sub-sitemap {s_url}: {e}")
                
    except Exception as e:
        print(f"Critical error in Doanh Nhân Sài Gòn process: {e}")

def scrape_page(url, storage, progress, session, global_settings):
    try:
        if not url.endswith('.html'):
            return
            
        print(f"Scraping: {url}")
        resp = session.get(url, timeout=30)
        if resp.status_code != 200:
            return
            
        soup = BeautifulSoup(resp.text, 'html.parser')
        
        # Title selection
        title_elem = soup.find('h1')
        if not title_elem:
            return
        title = title_elem.get_text(strip=True)
        
        # Content selection
        article_elem = soup.select_one('div.content, article.entry, .b-maincontent article, .detail-content, .post-content')
        
        if not article_elem:
            # Fallback
            article_elem = soup.select_one('.entry')
            
        if not article_elem:
            print(f"  Debug: No content found for {url}")
            return
            
        # Remove unwanted elements like ads, related news boxes inside content
        for unwanted in article_elem.select('.c-box, .c-main-banner, .sc-longform-header-meta, .c-share-scroll'):
            unwanted.decompose()
            
        content_md = md(str(article_elem), heading_style="ATX")
        
        # Extract metadata
        meta_date = ""
        date_elem = soup.select_one('meta[property="article:published_time"]')
        if date_elem:
            meta_date = date_elem.get('content', "")
        
        # Insert into SQLite
        success = storage.insert_article(
            url=url,
            title=title,
            content=content_md,
            metadata={"published_time": meta_date, "type": "Article"}
        )
        
        if success:
            if 'scraped_urls' not in progress: progress['scraped_urls'] = {}
            progress['scraped_urls'][url] = time.time()
        
        # Rate limiting
        time.sleep(global_settings.get('request_delay', 1.0))
        
    except Exception as e:
        print(f"Error scraping {url}: {e}")
