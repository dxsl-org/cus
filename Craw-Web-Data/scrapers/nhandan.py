import requests
from bs4 import BeautifulSoup
import json
import os
import time
from markdownify import markdownify as md
from scrapers.storage_utils import get_storage

def process(source, global_settings):
    """
    Main entry point for the nhandan.vn scraper.
    Iterates through sitemaps and scrapes articles.
    """
    base_url = "https://nhandan.vn"
    sitemap_index_url = f"{base_url}/sitemap.xml"
    source_id = source['id']
    storage = get_storage(source_id)
    
    user_agent = global_settings.get('user_agent', 'Mozilla/5.0')
    request_delay = global_settings.get('request_delay', 0.5)
    
    headers = {'User-Agent': user_agent}
    
    progress_file = f"scraped_nhandan_sitemaps_{source_id}.json"
    progress = {}
    if os.path.exists(progress_file):
        with open(progress_file, 'r', encoding='utf-8') as f:
            progress = json.load(f)
            
    print(f"Starting Nhan Dan deep scan (Source: {source_id})...")
    
    try:
        response = requests.get(sitemap_index_url, headers=headers, timeout=30)
        response.raise_for_status()
        soup = BeautifulSoup(response.content, 'xml')
        
        # Find all monthly news sitemaps
        # Example: <loc>https://nhandan.vn/news-2025-1.xml</loc>
        sitemap_locs = [loc.text for loc in soup.find_all('loc') if 'news-' in loc.text]
        
        # Sort sitemaps to process from oldest to newest (optional, but good for archiving)
        # news-2025-1.xml -> sort by parts
        def sort_key(url):
            filename = url.split('/')[-1] # news-2025-1.xml
            parts = filename.replace('.xml', '').split('-') # ['news', '2025', '1']
            if len(parts) >= 3:
                return int(parts[1]), int(parts[2])
            return 0, 0
            
        sitemap_locs.sort(key=sort_key)
        
        print(f"Found {len(sitemap_locs)} monthly news sitemaps.")
        
        for sitemap_url in sitemap_locs:
            if sitemap_url in progress.get('completed_sitemaps', []):
                print(f"Skipping completed sitemap: {sitemap_url}")
                continue
                
            print(f"Processing sitemap: {sitemap_url}")
            scrape_sitemap(sitemap_url, headers, storage, progress, request_delay)
            
            # Mark sitemap as completed
            if 'completed_sitemaps' not in progress:
                progress['completed_sitemaps'] = []
            progress['completed_sitemaps'].append(sitemap_url)
            
            with open(progress_file, 'w', encoding='utf-8') as f:
                json.dump(progress, f, indent=2)
                
    except Exception as e:
        print(f"Error in Nhan Dan process: {e}")

def scrape_sitemap(sitemap_url, headers, storage, progress, delay):
    """Scrapes all articles within a single month's sitemap."""
    try:
        response = requests.get(sitemap_url, headers=headers, timeout=30)
        response.raise_for_status()
        soup = BeautifulSoup(response.content, 'xml')
        
        urls = [loc.text for loc in soup.find_all('loc')]
        print(f"Found {len(urls)} articles in sitemap.")
        
        for url in urls:
            if url in progress.get('scraped_urls', {}):
                continue
                
            try:
                scrape_article(url, headers, storage, progress)
                # Keep tracked for current session to avoid duplicates if sitemap overlaps
                if 'scraped_urls' not in progress:
                    progress['scraped_urls'] = {}
                progress['scraped_urls'][url] = time.time()
                
                time.sleep(delay)
            except Exception as e:
                print(f"Failed to scrape article {url}: {e}")
                
    except Exception as e:
        print(f"Failed to process sitemap {sitemap_url}: {e}")

def scrape_article(url, headers, storage, progress):
    """Scrapes a single article page."""
    print(f"Scraping: {url}")
    response = requests.get(url, headers=headers, timeout=20)
    response.raise_for_status()
    
    soup = BeautifulSoup(response.content, 'html.parser')
    
    # Selectors based on nhandan_sample.html
    title_elem = soup.select_one('h1.article__title')
    if not title_elem:
        # Fallback to any h1
        title_elem = soup.find('h1')
        
    if not title_elem:
        print(f"No title found for {url}")
        return
        
    title = title_elem.get_text(strip=True)
    
    # Sapo (Lead paragraph)
    sapo = ""
    sapo_meta = soup.find('meta', attrs={'name': 'description'})
    if sapo_meta:
        sapo = sapo_meta.get('content', '')
        
    # Content
    content_el = soup.select_one('.article__body')
    if not content_el:
        content_el = soup.select_one('.detail-content-body')
        
    if not content_el:
        print(f"No content found for {url}")
        return
        
    # Remove unwanted elements
    for unwanted in content_el.select('.sda_middle, .article-relate, script, style'):
        unwanted.decompose()
        
    content_md = md(str(content_el), heading_style="ATX")
    
    # Metadata
    author = ""
    author_el = soup.select_one('.article__author .name')
    if author_el:
        author = author_el.get_text(strip=True)
    elif soup.select_one('.article__author'):
        author = soup.select_one('.article__author').get_text(strip=True)
        
    publish_date = ""
    time_el = soup.select_one('time.time')
    if time_el and time_el.get('datetime'):
        publish_date = time_el['datetime']
        
    tags = [a.get_text(strip=True) for a in soup.select('.article__tag a')]
    
    metadata = {
        "sapo": sapo,
        "author": author,
        "publish_date": publish_date,
        "tags": tags,
        "source": "nhandan.vn"
    }
    
    # Insert into SQLite
    storage.insert_article(
        url=url,
        title=title,
        content=content_md,
        metadata=metadata
    )
