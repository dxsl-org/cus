import os
import json
import time
import requests
from bs4 import BeautifulSoup
from markdownify import markdownify as md
from scrapers.storage_utils import get_storage

def process(source, global_settings):
    print("DEBUG: Starting PhunuOnline process function")
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

    sitemap_index_url = "https://www.phunuonline.com.vn/sitemap.xml"
    print(f"Fetching sitemap index from {sitemap_index_url}")
    
    try:
        headers = {
            "User-Agent": global_settings.get('user_agent', 'Mozilla/5.0')
        }
        r = requests.get(sitemap_index_url, headers=headers, timeout=30)
        if r.status_code != 200:
            print(f"Failed to fetch sitemap index: {r.status_code}")
            return
            
        index_soup = BeautifulSoup(r.text, 'xml')
        # Monthly sitemaps like news-2026-03.xml
        sitemap_urls = [loc.text for loc in index_soup.find_all('loc') if 'news-' in loc.text]
        
        print(f"Found {len(sitemap_urls)} monthly sitemaps.")
        if not sitemap_urls:
            print(f"DEBUG: No sub-sitemaps found containing 'news-' in {sitemap_index_url}")
            print(f"DEBUG: Sitemap content snippet: {r.text[:500]}")
        
        # Process monthly sitemaps (usually chronological)
        for s_url in reversed(sitemap_urls):
            print(f"Processing sub-sitemap: {s_url}")
            sub_r = requests.get(s_url, headers=headers, timeout=30)
            if sub_r.status_code != 200:
                continue
                
            sub_soup = BeautifulSoup(sub_r.text, 'xml')
            article_urls = [loc.text for loc in sub_soup.find_all('loc')]
            
            count = 0
            for url in reversed(article_urls):
                if url in progress['scraped_urls']:
                    continue
                
                success = scrape_article(url, storage, progress, global_settings)
                if success:
                    count += 1
                    if count % 10 == 0:
                        print(f"  Progress: {count} articles scraped from this sitemap.")
                        with open(progress_file, 'w', encoding='utf-8') as f:
                            json.dump(progress, f, indent=2)
                
                time.sleep(global_settings.get('request_delay', 0.5))
            
            # Save after each sub-sitemap
            with open(progress_file, 'w', encoding='utf-8') as f:
                json.dump(progress, f, indent=2)
                        
    except Exception as e:
        print(f"Error in Phụ Nữ Online process: {e}")
        
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
        
        title_el = soup.find('h1')
        title = title_el.get_text(strip=True) if title_el else ""
        
        content_el = soup.select_one('.content-fck')
        if not content_el:
            content_el = soup.select_one('.content')
        if not content_el:
            return False
            
        # Clean up unwanted elements if necessary
        for el in content_el.select('.tags, .related, script, style'):
            el.decompose()
            
        # Convert to markdown
        content_md = md(str(content_el), heading_style="ATX")
        
        # Metadata
        date_el = soup.select_one('.publish-date, .time')
        date_str = date_el.get_text(strip=True) if date_el else ""
        
        author_el = soup.select_one('.author')
        author = author_el.get_text(strip=True) if author_el else "Phụ Nữ Online"

        success = storage.insert_article(
            url=url,
            title=title,
            author=author,
            content=content_md,
            metadata={"publish_date": date_str}
        )
        
        if success:
            progress['scraped_urls'][url] = time.time()
            return True
            
    except Exception as e:
        print(f"Failed to scrape {url}: {e}")
    return False
