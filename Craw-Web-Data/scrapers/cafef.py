"""
CafeF Scraper Plugin (Sitemap Version)
"""
import os
import json
import time
import requests
from bs4 import BeautifulSoup
from markdownify import markdownify as md
from scrapers.storage_utils import get_storage

def process(source, global_settings):
    storage = get_storage(source['id'])
    headers = {'User-Agent': global_settings.get('user_agent', 'Mozilla/5.0')}
    progress_file = f"progress_{source['id']}.json"
    
    # Load progress
    progress = {}
    if os.path.exists(progress_file):
        with open(progress_file, 'r', encoding='utf-8') as f:
            progress = json.load(f)
    
    if 'processed_urls' not in progress:
        progress['processed_urls'] = []
    if 'processed_sitemaps' not in progress:
        progress['processed_sitemaps'] = []

    print(f"Starting CafeF sitemap discovery...")
    
    sitemap_index_url = "https://cafef.vn/sitemap.xml"
    try:
        resp = requests.get(sitemap_index_url, headers=headers, timeout=30)
        if resp.status_code != 200:
            print(f"Failed to fetch sitemap index: {resp.status_code}")
            return
            
        soup = BeautifulSoup(resp.text, 'xml')
        sitemaps = [loc.text for loc in soup.find_all('loc')]
        
        # Filter for article sitemaps (containing year-month-day pattern or latest)
        article_sitemaps = [s for s in sitemaps if 'sitemaps-' in s or 'latest-news-sitemap' in s]
        
        # Process latest first for quick results, then historical
        article_sitemaps.sort(reverse=True) 

        for sm_url in article_sitemaps:
            if sm_url in progress['processed_sitemaps']:
                continue
                
            print(f"Processing sitemap: {sm_url}")
            sm_resp = requests.get(sm_url, headers=headers, timeout=30)
            if sm_resp.status_code != 200:
                print(f"Failed to fetch sitemap: {sm_url}")
                continue
                
            sm_soup = BeautifulSoup(sm_resp.text, 'xml')
            urls = [loc.text for loc in sm_soup.find_all('loc')]
            
            new_articles = 0
            for url in urls:
                if url in progress['processed_urls']:
                    continue
                
                scrape_article(url, storage, progress, headers, global_settings)
                progress['processed_urls'].append(url)
                new_articles += 1
                
                # Periodically save progress
                if new_articles % 10 == 0:
                    with open(progress_file, 'w', encoding='utf-8') as f:
                        json.dump(progress, f, indent=2)
                
                time.sleep(global_settings.get('request_delay', 1))
            
            progress['processed_sitemaps'].append(sm_url)
            with open(progress_file, 'w', encoding='utf-8') as f:
                json.dump(progress, f, indent=2)
            
            print(f"Sitemap {sm_url} finished. Scraped {new_articles} new articles.")

    except Exception as e:
        print(f"Error in CafeF process: {e}")

def scrape_article(url, storage, progress, headers, global_settings):
    try:
        resp = requests.get(url, headers=headers, timeout=30)
        if resp.status_code != 200:
            return
            
        soup = BeautifulSoup(resp.text, 'html.parser')
        
        title_el = soup.select_one('h1.title')
        title = title_el.text.strip() if title_el else "No Title"
        
        sapo_el = soup.select_one('h2.sapo')
        sapo = sapo_el.text.strip() if sapo_el else ""
        
        # Meta tags for additional info
        author = ""
        author_meta = soup.find('meta', property='article:author')
        if author_meta:
            author = author_meta.get('content', '')
            
        pub_date = ""
        date_meta = soup.find('meta', property='article:published_time')
        if date_meta:
            pub_date = date_meta.get('content', '')
        
        # Content extraction
        content_el = soup.select_one('div.detail-content') or soup.select_one('div.contentdetail')
        if not content_el:
            return

        # Clean up unwanted elements
        for unwanted in content_el.select('.link-links, .sharemxh, .share-fb, .share-zalo, .af-tts, script, style'):
            unwanted.decompose()
            
        content_md = md(str(content_el), heading_style="ATX").strip()
        
        if sapo:
            content_md = f"**{sapo}**\n\n{content_md}"

        storage.insert_article(
            url=url, 
            title=title, 
            author=author,
            content=content_md, 
            metadata={
                "source": "CafeF",
                "published_at": pub_date,
                "sapo": sapo
            }
        )
    except Exception as e:
        print(f"Error scraping CafeF article {url}: {e}")
