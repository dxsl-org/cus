import os
import json
import time
import requests
from bs4 import BeautifulSoup
from markdownify import markdownify as md
from scrapers.storage_utils import get_storage

CATEGORIES = [
    {"name": "Chính trị", "id": "102442"},
    {"name": "Kinh tế", "id": "1027"},
    {"name": "Văn hóa", "id": "102249"},
    {"name": "Xã hội", "id": "102449"},
    {"name": "Khoa giáo", "id": "102451"},
    {"name": "Quốc tế", "id": "1026"}
]

def process(source, global_settings):
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

    headers = {
        "User-Agent": global_settings.get('user_agent', 'Mozilla/5.0')
    }

    print(f"Starting Báo Chính Phủ crawler (AJAX/Timeline-based)")
    
    try:
        for cat in CATEGORIES:
            print(f"Crawling category: {cat['name']} (ID: {cat['id']})")
            # Crawl through available pages
            for page in range(1, 501):
                # AJAX endpoint: https://baochinhphu.vn/timelinelist/{category_id}/{page}.htm
                api_url = f"https://baochinhphu.vn/timelinelist/{cat['id']}/{page}.htm"
                
                print(f"  Fetching API page {page}: {api_url}")
                r = requests.get(api_url, headers=headers, timeout=30)
                if r.status_code != 200:
                    print(f"    Failed to fetch page {page}: {r.status_code}")
                    break
                    
                # The response is a snippet of HTML
                soup = BeautifulSoup(r.text, 'html.parser')
                links = soup.find_all('a', href=True)
                
                article_urls = []
                for link in links:
                    href = link['href']
                    if '.htm' in href and not '/timelinelist/' in href:
                        if href.startswith('/'):
                            article_urls.append("https://baochinhphu.vn" + href)
                        elif href.startswith('http'):
                            article_urls.append(href)
                
                article_urls = list(set(article_urls))
                if not article_urls:
                    print("    No more articles found in this category.")
                    break
                    
                count = 0
                for url in article_urls:
                    if url in progress['scraped_urls']:
                        continue
                        
                    success = scrape_article(url, storage, progress, global_settings)
                    if success:
                        count += 1
                
                print(f"    Scraped {count} new articles from page {page}.")
                with open(progress_file, 'w', encoding='utf-8') as f:
                    json.dump(progress, f, indent=2)
                
                time.sleep(global_settings.get('request_delay', 1))

    except Exception as e:
        print(f"Error in Báo Chính Phủ process: {e}")
        
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
        
        title_el = soup.select_one('h1.detail-title')
        if not title_el: title_el = soup.find('h1')
        title = title_el.get_text(strip=True) if title_el else ""
        
        content_el = soup.select_one('.detail-content')
        if not content_el:
            content_el = soup.select_one('.post-content, #main-detail-content')
            
        if not content_el:
            return False
            
        # Sapo
        sapo_el = soup.select_one('h2.detail-sapo')
        sapo = sapo_el.get_text(strip=True) if sapo_el else ""
            
        # Convert to markdown
        content_md = ""
        if sapo:
            content_md += f"> {sapo}\n\n"
        content_md += md(str(content_el), heading_style="ATX")
        
        # Metadata
        date_el = soup.select_one('.detail-meta')
        date_str = date_el.get_text(strip=True) if date_el else ""
        
        success = storage.insert_article(
            url=url,
            title=title,
            content=content_md,
            metadata={"publish_date": date_str, "sapo": sapo}
        )
        
        if success:
            progress['scraped_urls'][url] = time.time()
            return True
            
    except Exception as e:
        print(f"Failed to scrape {url}: {e}")
    return False
