"""
Tuoi Tre Sitemap Scraper Plugin
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
    base_url = "https://tuoitre.vn"
    sitemap_index_url = "https://tuoitre.vn/sitemaps/index.rss"
    progress_file = source.get('progress_file', 'progress_tuoitre.json')
    
    # Initialize SQLite Storage
    storage = get_storage(source['id'])
    
    progress = {}
    if os.path.exists(progress_file):
        with open(progress_file, 'r', encoding='utf-8') as f:
            progress = json.load(f)
            
    headers = {
        "User-Agent": global_settings.get('user_agent', "Mozilla/5.0")
    }
    
    print(f"Starting Tuoi Tre sitemap crawler (Storage: SQLite)...")
    
    try:
        # Step 1: Get sitemap index
        r = requests.get(sitemap_index_url, headers=headers, timeout=30)
        soup = BeautifulSoup(r.text, 'xml')
        sitemaps = [s.find('loc').text for s in soup.find_all('sitemap') if s.find('loc')]
        
        # We usually only care about the most recent ones for new data, 
        # but for a full rebuild, we'd scan more.
        # Let's focused on the last 3 sitemaps for now to be safe.
        for sitemap_url in reversed(sitemaps[-3:]):
            if sitemap_url in progress.get('scanned_sitemaps', []):
                continue
                
            print(f"Scanning sitemap: {sitemap_url}")
            sr = requests.get(sitemap_url, headers=headers, timeout=30)
            ssoup = BeautifulSoup(sr.text, 'xml')
            urls = [u.find('loc').text for u in ssoup.find_all('url') if u.find('loc')]
            
            for url in urls:
                if url in progress.get('scraped_urls', {}):
                    continue
                
                # Filter out non-article URLs if possible
                if not re.search(r'\d{10,}\.htm', url):
                    continue
                
                scrape_article(url, storage, progress, headers, global_settings)
                
                # Save progress periodically
                if len(progress.get('scraped_urls', {})) % 10 == 0:
                    with open(progress_file, 'w', encoding='utf-8') as f:
                        json.dump(progress, f, indent=2)
            
            if 'scanned_sitemaps' not in progress: progress['scanned_sitemaps'] = []
            progress['scanned_sitemaps'].append(sitemap_url)
            
    except Exception as e:
        print(f"Error in Tuoi Tre process: {e}")

def download_comments(article_id, headers, global_settings):
    """Download comments for a Tuoi Tre article and return as list."""
    api_url = "https://id.tuoitre.vn/api/getlist-comment.api"
    app_key = "lHLShlUMAshjvNkHmBzNqERFZammKUXB1DjEuXKfWAwkunzW6fFbfrhP/IG0Xwp7aPwhwIuucLW1TVC9lzmUoA=="
    
    page = 1
    all_comments = []
    
    headers_copy = headers.copy()
    headers_copy.update({
        "Referer": f"https://tuoitre.vn/detail-{article_id}.htm",
        "X-Requested-With": "XMLHttpRequest",
        "Origin": "https://tuoitre.vn"
    })
    
    while True:
        params = {
            "pageindex": page,
            "pagesize": 5,
            "objId": article_id,
            "objType": 1,
            "objectpopupid": "",
            "sort": 2,
            "commentid": "",
            "command": "",
            "appKey": app_key
        }
        try:
            resp = requests.get(api_url, params=params, headers=headers_copy, timeout=20)
            if resp.status_code != 200:
                break
                
            data = resp.json()
            if not data.get('Success'):
                break
                
            if not data.get('Data'):
                break
                
            # Data is a stringified JSON array
            comments_batch = json.loads(data['Data'])
            if not comments_batch:
                break
                
            all_comments.extend(comments_batch)
            page += 1
            time.sleep(0.5)
        except Exception:
            break
            
    return all_comments

def scrape_article(url, storage, progress, headers, global_settings):
    try:
        print(f"Scraping: {url}")
        article_id = url.split('-')[-1].split('.')[0]
        
        resp = requests.get(url, headers=headers, timeout=30)
        if resp.status_code != 200:
            return
            
        soup = BeautifulSoup(resp.text, 'html.parser')
        
        category = soup.select_one('.detail-cate a').text.strip() if soup.select_one('.detail-cate a') else "Khác"
        title = soup.select_one('.detail-title').text.strip() if soup.select_one('.detail-title') else "No Title"
        sapo = soup.select_one('.detail-sapo').text.strip() if soup.select_one('.detail-sapo') else ""
        content_el = soup.select_one('.detail-content')
        author = soup.select_one('.author-info .author-name').text.strip() if soup.select_one('.author-info .author-name') else "Unknown"
        date_str = soup.select_one('.detail-time div').text.strip() if soup.select_one('.detail-time div') else ""
        
        if not content_el:
            return
            
        # Cleanup content
        for unwanted in content_el.select('script, style, .sidebar, .ads, .form-gop-y'):
            unwanted.decompose()
            
        content_md = md(str(content_el), heading_style="ATX")
        if sapo:
            content_md = f"> {sapo}\n\n" + content_md

        # Download comments
        comments = download_comments(article_id, headers, global_settings)
            
        # Insert into SQLite
        success = storage.insert_article(
            url=url,
            title=title,
            author=author,
            content=content_md,
            category=category,
            comments=comments,
            metadata={
                "date": date_str,
                "sapo": sapo
            }
        )
        
        if success:
            if 'scraped_urls' not in progress: progress['scraped_urls'] = {}
            progress['scraped_urls'][url] = time.time()
        
        time.sleep(global_settings.get('request_delay', 1))
        
    except Exception as e:
        print(f"Error scraping article {url}: {e}")
