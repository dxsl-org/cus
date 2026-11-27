import os
import json
import time
from bs4 import BeautifulSoup
from curl_cffi import requests
from markdownify import markdownify as md
from urllib.parse import urljoin
from scrapers.storage_utils import get_storage

def process_xenforo(source, global_settings, boards, base_url, forum_path="/forums/", thread_path="/threads/", max_pages=10):
    storage = get_storage(source['id'])
    progress_file = source.get('progress_file', f"progress_{source.get('id')}.json")
    
    progress = {}
    if os.path.exists(progress_file):
        try:
            with open(progress_file, 'r', encoding='utf-8') as f:
                progress = json.load(f)
        except: pass
            
    if 'scraped_urls' not in progress: progress['scraped_urls'] = {}

    print(f"Starting Xenforo crawler for {source['name']} (limit: {max_pages} pages)")
    session = requests.Session(impersonate="chrome110")
    
    try:
        for board in boards:
            print(f"Crawling board: {board}")
            for p in range(1, max_pages + 1):
                p_suffix = f"page-{p}" if p > 1 else ""
                # Ensure board part doesn't have leading slash if forum_path has trailing
                board_clean = board.lstrip('/')
                board_url = f"{base_url.rstrip('/')}{forum_path}{board_clean}/{p_suffix}"
                
                r = session.get(board_url, timeout=20)
                if r.status_code != 200: break
                    
                soup = BeautifulSoup(r.text, 'html.parser')
                # Xenforo thread links usually in .structItem-title a
                links = soup.select('.structItem-title a, .thread-title a')
                
                thread_urls = []
                for link in links:
                    href = link.get('href')
                    if href and thread_path in href:
                        thread_urls.append(urljoin(base_url, href))
                        
                thread_urls = list(set(thread_urls))
                print(f"    Found {len(thread_urls)} threads on page {p}")
                if not thread_urls: break
                
                count = 0
                for url in thread_urls:
                    clean_url = url.split('/unread')[0].split('#')[0]
                    if clean_url in progress['scraped_urls']: continue
                        
                    success = scrape_thread(session, clean_url, storage, progress)
                    if success:
                        count += 1
                        
                print(f"    Scraped {count} new threads from page {p}.")
                with open(progress_file, 'w', encoding='utf-8') as f:
                    json.dump(progress, f, indent=2)
                
                time.sleep(global_settings.get('request_delay', 1.0))
                
    except Exception as e:
        print(f"Error in Xenforo process for {base_url}: {e}")

def scrape_thread(session, url, storage, progress):
    try:
        resp = session.get(url, timeout=20)
        if resp.status_code != 200: return False
            
        soup = BeautifulSoup(resp.text, 'html.parser')
        title_el = soup.select_one('.p-title-value, h1, .thread-title')
        title = ""
        if title_el:
            title = title_el.get_text(strip=True)
        elif soup.title:
            title = soup.title.get_text(strip=True).split('|')[0].strip()
            
        if not title: return False
            
        messages = soup.select('article.message, .message, .post-container')
        if not messages: return False
            
        thread_content = ""
        for idx, msg in enumerate(messages):
            author_el = msg.select_one('.message-name, .username, .author')
            author = author_el.get_text(strip=True) if author_el else "Unknown"
            
            body_el = msg.select_one('.message-body .bbWrapper, .message-content, .message-body, .post-body')
            if not body_el: continue
                
            for quote in body_el.select('.bbCodeBlock--quote, blockquote'):
                quote.decompose()
                
            body_md = md(str(body_el), heading_style="ATX").strip()
            
            if idx == 0:
                thread_content += f"**{author}** hỏi/chia sẻ:\n\n{body_md}\n\n---\n"
            else:
                thread_content += f"**{author}** trả lời:\n\n{body_md}\n\n---\n"
                
        date_el = soup.select_one('.u-dt[title], time')
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
