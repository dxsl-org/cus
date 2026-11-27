import os
import json
import time
import random
from bs4 import BeautifulSoup
from curl_cffi import requests
from markdownify import markdownify as md
from scrapers.storage_utils import get_storage

def get_headers(global_settings):
    user_agents = [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36"
    ]
    return {
        "User-Agent": random.choice(user_agents),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "vi-VN,vi;q=0.9,en-US;q=0.8,en;q=0.7"
    }

def process(source, global_settings):
    storage = get_storage(source['id'])
    progress_file = source.get('progress_file', f"progress_{source.get('id')}.json")
    
    progress = {}
    if os.path.exists(progress_file):
        try:
            with open(progress_file, 'r', encoding='utf-8') as f:
                progress = json.load(f)
        except:
            pass
            
    if 'scraped_urls' not in progress:
        progress['scraped_urls'] = {}

    print(f"Starting Nghiencuuquocte crawler (Sitemap-based)")
    session = requests.Session(impersonate="chrome110")
    
    try:
        # 1. Get the sitemap index
        index_url = "https://nghiencuuquocte.org/sitemap_index.xml"
        print(f"Fetching sitemap index: {index_url}")
        r = session.get(index_url, headers=get_headers(global_settings), timeout=30)
        if r.status_code != 200:
            print(f"Failed to fetch sitemap index: {r.status_code}")
            return
            
        soup_index = BeautifulSoup(r.text, 'xml')
        sitemap_urls = [loc.text for loc in soup_index.find_all('loc') if 'post-sitemap' in loc.text]
        
        print(f"Found {len(sitemap_urls)} post sitemaps.")
        
        # Crawl each sub-sitemap
        for sm_url in sitemap_urls:
            print(f"  Fetching sitemap: {sm_url}")
            try:
                r_sm = session.get(sm_url, headers=get_headers(global_settings), timeout=30)
                if r_sm.status_code != 200:
                    continue
                    
                soup_sm = BeautifulSoup(r_sm.text, 'xml')
                article_urls = [loc.text for loc in soup_sm.find_all('loc')]
                
                print(f"    Found {len(article_urls)} articles in this sitemap.")
                
                count = 0
                for url in article_urls:
                    if url in progress['scraped_urls']:
                        continue
                        
                    success = scrape_article(session, url, storage, progress, global_settings)
                    if success:
                        count += 1
                        with open(progress_file, 'w', encoding='utf-8') as f:
                            json.dump(progress, f, indent=2)
                            
                    time.sleep(global_settings.get('request_delay', 1.5))
                
                print(f"    Scraped {count} new articles from {sm_url}.")
                
            except Exception as e:
                print(f"  Error fetching sitemap {sm_url}: {e}")
                
            time.sleep(2)

    except Exception as e:
        print(f"Error in Nghiencuuquocte process: {e}")
        
def scrape_article(session, url, storage, progress, global_settings):
    try:
        resp = session.get(url, headers=get_headers(global_settings), timeout=20)
        if resp.status_code != 200:
            print(f"    Failed to fetch {url}: {resp.status_code}")
            return False
            
        soup = BeautifulSoup(resp.text, 'html.parser')
        
        title_el = soup.find('h1', class_='entry-title')
        title = title_el.get_text(strip=True) if title_el else ""
        if not title:
            return False
            
        content_el = soup.find('div', class_='entry-content')
        if not content_el:
            return False
            
        # Clean up unwanted elements (like "Share this", related posts)
        for el in content_el.select('.sharedaddy, .related-posts, script, style, .yarpp-related'):
            el.decompose()
            
        content_md = md(str(content_el), heading_style="ATX")
        
        date_el = soup.select_one('.entry-date')
        date_str = date_el.get_text(strip=True) if date_el and date_el.get_text() else ""
        
        success = storage.insert_article(
            url=url,
            title=title,
            content=content_md,
            metadata={"publish_date": date_str}
        )
        
        if success:
            progress['scraped_urls'][url] = time.time()
            return True
            
    except Exception as e:
        print(f"    Failed to scrape {url}: {e}")
    return False
