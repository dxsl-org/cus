import os
import json
import time
from bs4 import BeautifulSoup
from curl_cffi import requests
from markdownify import markdownify as md
from scrapers.storage_utils import get_storage

# Public, high-value technical/advice boards on Otofun
BOARDS = [
    "tu-van-mua-xe-ban-xe.88",
    "chia-se-kinh-nghiem-lai-xe.303",
    "cac-cum-pan-benh-cach-sua-chua-bao-duong.305",
    "honda.31",
    "toyota.38",
    "ford.35",
    "hyundai.36",
    "an-toan-giao-thong-tinh-huong-tren-duong.65",
    "luat-canh-sat-giao-thong-va-cac-van-de-tren-duong.9"
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
            pass
            
    if 'scraped_urls' not in progress:
        progress['scraped_urls'] = {}

    print(f"Starting Otofun crawler (Category-based)")
    session = requests.Session(impersonate="chrome110")
    
    try:
        for board in BOARDS:
            print(f"Crawling board: {board}")
            # Crawl up to 50 pages per board for recent threads
            for p in range(1, 51):
                board_url = f"https://www.otofun.net/forums/{board}/page-{p}" if p > 1 else f"https://www.otofun.net/forums/{board}/"
                print(f"  Fetching: {board_url}")
                
                r = session.get(board_url, timeout=20)
                if r.status_code != 200:
                    print(f"    Failed or reached end at page {p}.")
                    break
                    
                soup = BeautifulSoup(r.text, 'html.parser')
                links = soup.select('.structItem-title a')
                
                thread_urls = []
                for link in links:
                    href = link.get('href')
                    if href and href.startswith('/threads/'):
                        thread_urls.append('https://www.otofun.net' + href)
                        
                thread_urls = list(set(thread_urls))
                if not thread_urls:
                    print("    No thread links found.")
                    break
                
                count = 0
                for url in thread_urls:
                    # Remove trailing unread anchors (e.g. /unread)
                    clean_url = url.split('/unread')[0]
                    if clean_url in progress['scraped_urls']:
                        continue
                        
                    success = scrape_thread(session, clean_url, storage, progress)
                    if success:
                        count += 1
                        
                print(f"    Scraped {count} new threads from page {p}.")
                with open(progress_file, 'w', encoding='utf-8') as f:
                    json.dump(progress, f, indent=2)
                
                time.sleep(global_settings.get('request_delay', 1.5))
                
    except Exception as e:
        print(f"Error in Otofun process: {e}")

def scrape_thread(session, url, storage, progress):
    try:
        resp = session.get(url, timeout=20)
        if resp.status_code != 200:
            return False
            
        soup = BeautifulSoup(resp.text, 'html.parser')
        
        title_el = soup.select_one('.p-title-value')
        title = title_el.get_text(strip=True) if title_el else ""
        if not title:
            return False
            
        # Extract all messages (post and replies) on the first page
        messages = soup.select('article.message')
        if not messages:
            return False
            
        thread_content = ""
        for idx, msg in enumerate(messages):
            author_el = msg.select_one('.message-name')
            author = author_el.get_text(strip=True) if author_el else "Unknown"
            
            body_el = msg.select_one('.message-body')
            if not body_el:
                continue
                
            # remove quotes of previous posts for cleaner reading
            for quote in body_el.select('.bbCodeBlock--quote'):
                quote.decompose()
                
            body_md = md(str(body_el), heading_style="ATX").strip()
            
            if idx == 0:
                thread_content += f"**{author}** hỏi/chia sẻ:\n\n{body_md}\n\n---\n"
            else:
                thread_content += f"**{author}** trả lời:\n\n{body_md}\n\n---\n"
                
        date_el = soup.select_one('.u-dt[title]')
        date_str = date_el['title'] if date_el and date_el.has_attr('title') else ""
        
        success = storage.insert_article(
            url=url,
            title=title,
            content=thread_content,
            metadata={"publish_date": date_str}
        )
        
        if success:
            progress['scraped_urls'][url] = time.time()
            return True

    except Exception as e:
        print(f"    Failed to scrape {url}: {e}")
    return False
