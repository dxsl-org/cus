import os
import json
import time
import requests
from bs4 import BeautifulSoup
from markdownify import markdownify as md
from scrapers.storage_utils import get_storage

CATEGORIES = [
    {"name": "Thời sự", "slug": "thoi-su"},
    {"name": "Trao đổi thực tiễn, kinh nghiệm xét xử", "slug": "traodoithuctienkinhnghiemxetxu"},
    {"name": "Nghiên cứu khoa học xét xử", "slug": "nghiencuukhoahocxetxu"},
    {"name": "Nghiên cứu - Xây dựng pháp luật", "slug": "nghien-cuu-xay-dung-phap-luat"},
    {"name": "Án lệ", "slug": "an-le"},
    {"name": "Xét xử - Bình luận án", "slug": "xet-xu"},
    {"name": "Nhân vật và sự kiện", "slug": "nhan-vat-su-kien"}
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

    print(f"Starting TapChiToaan crawler (Category-based)")
    
    try:
        for cat in CATEGORIES:
            print(f"Crawling category: {cat['name']} (/{cat['slug']})")
            # Crawl through available pages
            for p in range(1, 501):
                page_url = f"https://tapchitoaan.vn/{cat['slug']}"
                if p > 1:
                    page_url += f"?page={p}"
                
                print(f"  Fetching page {p}: {page_url}")
                r = requests.get(page_url, headers=headers, timeout=30)
                if r.status_code != 200:
                    print(f"    Failed to fetch page {p}: {r.status_code}")
                    break
                    
                soup = BeautifulSoup(r.text, 'html.parser')
                # Find article links
                # Selector: .post-entry-2 h3 a
                links = soup.select('.post-entry-2 h3 a')
                article_urls = []
                for link in links:
                    href = link.get('href')
                    if href and 'tapchitoaan.vn' in href:
                        article_urls.append(href)
                
                article_urls = list(set(article_urls))
                if not article_urls:
                    print("    No article links found on this page.")
                    break
                    
                count = 0
                for url in article_urls:
                    if url in progress['scraped_urls']:
                        continue
                        
                    success = scrape_article(url, storage, progress, global_settings)
                    if success:
                        count += 1
                
                print(f"    Scraped {count} new articles from page {p}.")
                with open(progress_file, 'w', encoding='utf-8') as f:
                    json.dump(progress, f, indent=2)
                
                time.sleep(global_settings.get('request_delay', 1))

    except Exception as e:
        print(f"Error in TapChiToaan process: {e}")
        
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
        
        title_el = soup.select_one('h1.title-detail')
        title = title_el.get_text(strip=True) if title_el else ""
        
        content_el = soup.select_one('.post-entry')
        if not content_el:
            return False
            
        # Clean up unwanted elements if necessary
        for el in content_el.select('.tags, .related, script, style'):
            el.decompose()
            
        # Convert to markdown
        content_md = md(str(content_el), heading_style="ATX")
        
        # Metadata
        date_el = soup.select_one('.single-post .pb-1')
        date_str = date_el.get_text(strip=True) if date_el else ""
        
        author_el = soup.select_one('.author')
        author = author_el.get_text(strip=True) if author_el else ""

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
