"""
Tuoi Tre Cuoi Tuan Scraper Plugin - Improved Version
"""
import os
import re
import json
import time
import requests
from bs4 import BeautifulSoup
from markdownify import markdownify as md
from urllib.parse import urljoin

from scrapers.storage_utils import get_storage

def process(source, global_settings):
    base_url = "https://cuoituan.tuoitre.vn"
    progress_file = source.get('progress_file', 'progress_cuoituan_tuoitre.json')
    
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

    # Sync with DB to mark existing articles as skipped
    try:
        conn = storage.get_connection() if hasattr(storage, 'get_connection') else None
        # Since StorageManager might not expose conn easily, we can use a direct approach if needed
        # but let's see if we can just query it through a helper or just let it be.
        # Actually, let's just use a direct sqlite3 call to populate if needed.
        import sqlite3
        conn = sqlite3.connect(storage.db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT url FROM articles")
        for row in cursor.fetchall():
            if row[0] not in progress['scraped_urls']:
                progress['scraped_urls'][row[0]] = time.time()
        conn.close()
        print(f"Synced with database: {len(progress['scraped_urls'])} articles known.")
    except Exception as e:
        print(f"Note: Could not sync with existing DB records: {e}")
            
    headers = {
        "User-Agent": global_settings.get('user_agent', "Mozilla/5.0"),
        "Referer": base_url,
        "X-Requested-With": "XMLHttpRequest"
    }
    
    print(f"Starting Tuoi Tre Cuoi Tuan crawler (Deep Scan via Timeline)...")
    
    # Timeline IDs discovered
    timeline_configs = [
        {"id": "200091", "name": "Vấn đề - Sự kiện"},
        {"id": "200092", "name": "Cuộc sống muôn màu"},
        {"id": "200090", "name": "Văn hóa - Giải trí"},
        {"id": "12", "name": "Kinh tế"},
        {"id": "10", "name": "Xã hội"},
        {"id": "17", "name": "Thế giới"}
    ]
    
    if 'scraped_urls' not in progress:
        progress['scraped_urls'] = {}

    for config in timeline_configs:
        t_id = config['id']
        t_name = config['name']
        print(f"\n--- Scanning Category: {t_name} (ID: {t_id}) ---")
        
        page = 1
        consecutive_empty = 0
        
        while page <= 1000: # Deep search limit
            timeline_url = f"{base_url}/timeline/{t_id}/trang-{page}.htm"
            print(f"Fetching page {page}: {timeline_url}")
            
            try:
                resp = requests.get(timeline_url, headers=headers, timeout=30)
                if resp.status_code != 200 or not resp.text.strip():
                    print(f"End of category or error at page {page}")
                    break
                
                soup = BeautifulSoup(resp.text, 'html.parser')
                articles = soup.select('article.news-item, .art-right-one')
                
                if not articles:
                    print(f"No more articles found in HTML on page {page}")
                    consecutive_empty += 1
                    if consecutive_empty >= 2: break
                    page += 1
                    continue
                
                print(f"Found {len(articles)} potential articles on page {page}")
                consecutive_empty = 0
                page_links = []
                for art in articles:
                    link_el = art.select_one('a[href*=".htm"]')
                    if link_el:
                        url = urljoin(base_url, link_el.get('href'))
                        if re.search(r'-\d+\.htm$', url):
                            page_links.append(url)
                
                if not page_links:
                    print(f"No valid article links found on page {page} out of {len(articles)} elements.")
                    # Try next page instead of breaking immediately for Tuoi Tre
                    page += 1
                    continue
                
                print(f"Discovered {len(page_links)} valid article links.")
                new_urls_on_page = 0
                already_scraped_on_page = 0
                
                for url in set(page_links):
                    if url in progress['scraped_urls']:
                        already_scraped_on_page += 1
                        continue
                        
                    success = scrape_article(url, storage, progress, headers, global_settings)
                    if success:
                        new_urls_on_page += 1
                        progress['scraped_urls'][url] = time.time()
                        
                        if new_urls_on_page % 5 == 0:
                            save_progress(progress_file, progress)
                
                print(f"Page {page} finished. New: {new_urls_on_page}, Skip: {already_scraped_on_page}")
                
                # Crucial Fix: Continue even if new_urls_on_page == 0 
                # to scan deep into historical archives which might have gaps.
                
                page += 1
                save_progress(progress_file, progress)
                
            except Exception as e:
                print(f"Error scanning page {page} for {t_name}: {e}")
                time.sleep(2)
                page += 1 # Continue on error
                continue

def save_progress(path, data):
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2)

def scrape_article(url, storage, progress, headers, global_settings):
    try:
        # print(f"Scraping: {url}")
        resp = requests.get(url, headers=headers, timeout=30)
        if resp.status_code != 200:
            print(f"Failed to fetch {url}: {resp.status_code}")
            return False
            
        soup = BeautifulSoup(resp.text, 'html.parser')
        
        # Selectors for Cuoi Tuan
        title_el = soup.select_one('.fck-title, .detail-title, h1, .title')
        if not title_el:
            print(f"Title not found for {url}")
            return False
            
        title = title_el.get_text(strip=True)
        sapo_el = soup.select_one('.article-summary, .detail-sapo, .sapo')
        sapo = sapo_el.get_text(strip=True) if sapo_el else ""
        
        content_el = soup.select_one('#sub_content, .detail-content, .fck, #fckContent')
        
        if not content_el:
            # Fallback if content is structured differently
            content_el = soup.select_one('article.fck, section.fck, div[itemprop="articleBody"]')
            
        if not content_el:
            print(f"Content not found for {url}")
            return False
            
        # Extract metadata
        author_el = soup.select_one('.tool-author strong, .author, .author-one')
        author = author_el.get_text(strip=True) if author_el else ""
        
        pub_date = ""
        date_el = soup.select_one('.tool-author span, .detail-time, .date, .list-one li span')
        if date_el:
            pub_date = date_el.get_text(strip=True)

        # Convert to Markdown
        content_md = md(str(content_el), heading_style="ATX")
        if sapo:
            content_md = f"> {sapo}\n\n" + content_md
            
        # Add metadata to markdown if needed
        if author:
            content_md = f"**Tác giả: {author}**\n\n" + content_md
        if pub_date:
            content_md = f"*Ngày đăng: {pub_date}*\n\n" + content_md

        # Insert into SQLite
        success = storage.insert_article(
            url=url,
            title=title,
            content=content_md,
            metadata={
                "sapo": sapo,
                "author": author,
                "publish_date": pub_date
            }
        )
        
        time.sleep(global_settings.get('request_delay', 1))
        return success
        
    except Exception as e:
        print(f"Error scraping article {url}: {e}")
        return False
