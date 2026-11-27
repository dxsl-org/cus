import os
import json
import time
from bs4 import BeautifulSoup
from curl_cffi import requests
from markdownify import markdownify as md
from urllib.parse import urljoin
from scrapers.storage_utils import get_storage

def process(source, global_settings):
    storage = get_storage(source['id'])
    progress_file = source.get('progress_file', f"progress_{source['id']}.json")
    
    progress = {}
    if os.path.exists(progress_file):
        try:
            with open(progress_file, 'r', encoding='utf-8') as f:
                progress = json.load(f)
        except: pass
            
    if 'scraped_urls' not in progress: progress['scraped_urls'] = {}
    
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/121.0.0.0 Safari/537.36"}
    session = requests.Session(impersonate="chrome110", headers=headers)
    
    print(f"Starting Vietjack crawler for {source['name']}")
    
    # Define primary landing pages for grades (Math, Lit, English etc)
    # We can crawl the homepage to find these or use a list.
    # Homepage links are very diverse. Let's start with a few major ones.
    start_urls = [
        "https://vietjack.com/soan-van-6-cd/index.jsp",
        "https://vietjack.com/soan-van-7-cd/index.jsp",
        "https://vietjack.com/soan-van-8-cd/index.jsp",
        "https://vietjack.com/giai-bt-toan-6-cd/index.jsp",
        "https://vietjack.com/giai-bt-toan-7-cd/index.jsp",
        "https://vietjack.com/giai-toan-8-cd/index.jsp",
    ]
    
    # Discovery phase: find more index.jsp pages from homepage
    try:
        r = session.get("https://vietjack.com/", timeout=20)
        soup = BeautifulSoup(r.text, 'html.parser')
        for a in soup.find_all('a', href=True):
            href = a['href']
            if ('soan-van' in href or 'giai-bt' in href or 'giai-toan' in href) and href.endswith('index.jsp'):
                start_urls.append(urljoin("https://vietjack.com/", href))
    except: pass
    
    start_urls = list(set(start_urls))
    print(f"Found {len(start_urls)} index pages to crawl.")
    
    count = 0
    for index_url in start_urls:
        print(f"Crawling index: {index_url}")
        try:
            r = session.get(index_url, timeout=20)
            soup = BeautifulSoup(r.text, 'html.parser')
            
            # Find lesson links
            # Usually links in the middle column
            lesson_links = []
            content_area = soup.select_one('.col-md-9, .middle-col')
            if content_area:
                for a in content_area.find_all('a', href=True):
                    href = a['href']
                    if href.endswith('.jsp') and 'index.jsp' not in href:
                        lesson_links.append(urljoin(index_url, href))
            
            lesson_links = list(set(lesson_links))
            print(f"  Found {len(lesson_links)} lessons.")
            
            for url in lesson_links:
                if url in progress['scraped_urls']: continue
                
                # Scrape lesson
                try:
                    r_l = session.get(url, timeout=20)
                    l_soup = BeautifulSoup(r_l.text, 'html.parser')
                    
                    title_el = l_soup.find('h1')
                    title = title_el.text.strip() if title_el else "No Title"
                    
                    content_el = l_soup.select_one('.middle-col, .col-md-9, .post-content')
                    if not content_el: continue
                    
                    # Remove ads, navigation from content_el
                    for junk in content_el.select('.adsbygoogle, .trangtruoc, .trangsau, script, style'):
                        junk.decompose()
                        
                    content_md = md(str(content_el), heading_style="ATX").strip()
                    
                    success = storage.insert_article(
                        url=url,
                        title=title,
                        content=content_md,
                        metadata={"source": "vietjack.com", "category_url": index_url}
                    )
                    
                    if success:
                        count += 1
                        progress['scraped_urls'][url] = time.time()
                        with open(progress_file, 'w', encoding='utf-8') as f:
                            json.dump(progress, f, indent=2)
                            
                except Exception as e:
                    print(f"    Error scraping {url}: {e}")
                
                time.sleep(global_settings.get('request_delay', 0.5))
                
        except Exception as e:
            print(f"  Error crawling index {index_url}: {e}")
            
    print(f"Finished Vietjack. Scraped {count} items.")
