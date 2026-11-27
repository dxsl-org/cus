"""
Tia Sang Scraper Plugin (Deep Scan Version)
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
    progress_file = source.get('progress_file', f"progress_{source.get('id')}.json")
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
    if 'offsets' not in progress:
        progress['offsets'] = {}
    
    headers = {
        "User-Agent": global_settings.get('user_agent', "Mozilla/5.0")
    }
    
    # Confirmed Category IDs for Tia Sang (VnExpress Site ID: 1006614)
    categories = {
        "Chuyên đề": "1006622",
        "Diễn đàn": "1006616",
        "Khoa học - Công nghệ": "1006617",
        "Văn hoá - Xã hội": "1006687",
        "Nền tảng - Kiến tạo": "1006710",
        "Hồ sơ - Nhân vật": "1006711"
    }
    
    site_id = "1006614"
    limit = 50 # API limit per request
    
    print(f"Starting API-based Deep Scan for {source['name']}")
    
    for cat_name, cat_id in categories.items():
        offset = progress['offsets'].get(cat_id, 0)
        print(f"Processing Category: {cat_name} (ID: {cat_id}) starting from offset {offset}")
        
        while True:
            api_url = f"https://gw.vnexpress.net/ar/get_rule_2?category_id={cat_id}&limit={limit}&offset={offset}&site_id={site_id}"
            try:
                r = requests.get(api_url, headers=headers, timeout=30)
                if r.status_code != 200:
                    print(f"API Error {r.status_code} for {cat_name}")
                    break
                    
                resp_data = r.json()
                if not resp_data or 'data' not in resp_data or cat_id not in resp_data['data']:
                    print(f"Finished or Empty data for {cat_name}")
                    break
                
                articles = resp_data['data'][cat_id].get('data', [])
                if not articles:
                    print(f"No more articles for {cat_name}")
                    break
                
                print(f"Found {len(articles)} articles in {cat_name} (Offset: {offset})")
                
                for art in articles:
                    url = art.get('share_url')
                    if not url: continue
                    
                    if url in progress['scraped_urls']:
                        continue
                    
                    scrape_article(url, storage, progress, headers, global_settings)
                    
                    # Periodic save of progress
                    if len(progress['scraped_urls']) % 10 == 0:
                        progress['offsets'][cat_id] = offset
                        with open(progress_file, 'w', encoding='utf-8') as f:
                            json.dump(progress, f, indent=2)

                offset += limit
                progress['offsets'][cat_id] = offset # Update progress with next offset
                
            except Exception as e:
                print(f"Error in discovery loop for {cat_name}: {e}")
                time.sleep(5)
                break # Break inner loop on recurring error

    # Final save
    with open(progress_file, 'w', encoding='utf-8') as f:
        json.dump(progress, f, indent=2)

def scrape_article(url, storage, progress, headers, global_settings):
    try:
        resp = requests.get(url, headers=headers, timeout=30)
        if resp.status_code != 200:
            return
            
        soup = BeautifulSoup(resp.text, 'html.parser')
        
        # High-fidelity selectors for Tia Sang (VnExpress-like)
        title_el = soup.select_one('h1.title-detail, h1')
        if not title_el:
            return
        title = title_el.get_text(strip=True)
        
        sapo_el = soup.select_one('p.description')
        sapo = sapo_el.get_text(strip=True) if sapo_el else ""
        
        content_el = soup.select_one('article.fck_detail, div.fck_detail')
        if not content_el:
            return

        # Meta extraction
        author = ""
        # Try finding author at the end of content
        author_el = content_el.select_one('p[style*="text-align:right"], .author, .detail__author')
        if author_el:
            author = author_el.get_text(strip=True)
        else:
            # Fallback to meta
            meta_author = soup.find('meta', attrs={'name': 'author'})
            if meta_author:
                author = meta_author.get('content', '')

        published_date = ""
        date_el = soup.select_one('span.date')
        if not date_el:
            date_meta = soup.find('meta', attrs={'name': 'pubdate'})
            if date_meta:
                published_date = date_meta.get('content', '')
        else:
            published_date = date_el.get_text(strip=True)
            
        tags = []
        tags_meta = soup.find('meta', attrs={'name': 'keywords'})
        if tags_meta:
            tags = [t.strip() for t in tags_meta.get('content', '').split(',')]

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
            metadata={'published_date': published_date, 'sapo': sapo}
        )
        
        if success:
            progress['scraped_urls'][url] = time.time()
            print(f"SUCCESS: {title[:50]}...")
        
        # Delay to respect robots.txt (though we are scanning full archives)
        time.sleep(global_settings.get('request_delay', 0.5))
        
    except Exception as e:
        print(f"Error scraping article {url}: {e}")
