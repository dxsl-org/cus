import os
import json
import time
import requests
from bs4 import BeautifulSoup
from markdownify import markdownify as md
from scrapers.storage_utils import get_storage

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

    print(f"Starting ChinhSachOnline crawler (Listing-based)")
    
    try:
        # Crawl through pagination
        p = 1
        while True:
            # Pattern: https://chinhsachonline.chinhphu.vn/danh-sach-cau-hoi/trang-{p}.htm
            if p == 1:
                page_url = "https://chinhsachonline.chinhphu.vn/danh-sach-cau-hoi.htm"
            else:
                page_url = f"https://chinhsachonline.chinhphu.vn/danh-sach-cau-hoi/trang-{p}.htm"
            
            print(f"  Fetching page {p}: {page_url}")
            r = requests.get(page_url, headers=headers, timeout=30)
            if r.status_code != 200:
                print(f"    Failed to fetch page {p} or reached end: {r.status_code}")
                break
                
            soup = BeautifulSoup(r.text, 'html.parser')
            # Look for detail links using the identified selector
            links = soup.select('div.content a')
            article_urls = []
            for link in links:
                href = link.get('href')
                # A valid article URL usually ends with a number like -84708.htm
                if href and '.htm' in href and '-' in href and any(char.isdigit() for char in href):
                    if href.startswith('/'):
                        article_urls.append("https://chinhsachonline.chinhphu.vn" + href)
                    elif href.startswith('http'):
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
            
            print(f"    Scraped {count} new Q&A from page {p}.")
            with open(progress_file, 'w', encoding='utf-8') as f:
                json.dump(progress, f, indent=2)
            
            p += 1
            # Removing the 5-page hard limit for continuous running
            if p > 500: # High safety limit
                print("    Reached high page limit safety check.")
                break
            
            time.sleep(global_settings.get('request_delay', 1))

    except Exception as e:
        print(f"Error in ChinhSachOnline process: {e}")
        
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
        
        # Selectors based on research
        title_el = soup.select_one('h1.detail__title')
        title = title_el.get_text(strip=True) if title_el else ""
        # Remove "Hỏi: " prefix if exists
        if title.lower().startswith("hỏi:"):
            title = title[4:].strip()
        
        q_el = soup.select_one('div.detail__cquestion')
        full_question = q_el.get_text(strip=True) if q_el else ""
        
        # Answer is in .detail__content, usually after a <p>Trả lời</p>
        content_el = soup.select_one('div.detail__content')
        if not content_el:
            return False
            
        # Category tag
        cat_el = soup.select_one('a.detail__category')
        category = cat_el.get_text(strip=True) if cat_el else ""

        # Format content: Full question + Answer
        content_md = f"### Câu hỏi\n{full_question}\n\n### Trả lời\n"
        
        # Clean up the content element (remove the category link and the "Trả lời" header if possible)
        for header in content_el.find_all(['p', 'div'], string=lambda s: s and 'Trả lời' in s):
            header.decompose()
        
        content_md += md(str(content_el), heading_style="ATX")
        
        success = storage.insert_article(
            url=url,
            title=title,
            content=content_md,
            metadata={
                "category": category,
                "full_question": full_question,
                "source": "ChinhSachOnline"
            }
        )
        
        if success:
            progress['scraped_urls'][url] = time.time()
            return True
            
    except Exception as e:
        print(f"Failed to scrape {url}: {e}")
    return False
