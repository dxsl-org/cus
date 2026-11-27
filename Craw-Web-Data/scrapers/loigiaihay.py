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
    
    print(f"Starting Loigiaihay crawler for {source['name']}")
    
    # Discovery from grade pages
    start_urls = [
        "https://loigiaihay.com/lop-12.html",
        "https://loigiaihay.com/lop-11.html",
        "https://loigiaihay.com/lop-10.html",
        "https://loigiaihay.com/lop-9.html",
        "https://loigiaihay.com/lop-8.html",
        "https://loigiaihay.com/lop-7.html",
        "https://loigiaihay.com/lop-6.html",
    ]
    
    # To find subject links
    subject_urls = []
    for grade_url in start_urls:
        try:
            r = session.get(grade_url, timeout=20)
            soup = BeautifulSoup(r.text, 'html.parser')
            for a in soup.find_all('a', href=True):
                href = a['href']
                # Subject links often look like /subject-lop-X-cX.html
                if '-c' in href and href.endswith('.html'):
                    subject_urls.append(urljoin(grade_url, href))
        except: pass
        
    subject_urls = list(set(subject_urls))
    print(f"Found {len(subject_urls)} subject pages.")
    
    count = 0
    for sub_url in subject_urls:
        print(f"Scanning subject: {sub_url}")
        try:
            r = session.get(sub_url, timeout=20)
            soup = BeautifulSoup(r.text, 'html.parser')
            
            # Lesson links are often in a list
            lesson_links = []
            for a in soup.find_all('a', href=True):
                href = a['href']
                # Lesson links are usually deeper .html files
                if href.endswith('.html') and '-c' not in href and '/lop-' not in href:
                    lesson_links.append(urljoin(sub_url, href))
            
            lesson_links = list(set(lesson_links))
            print(f"  Found {len(lesson_links)} potential lessons.")
            
            for url in lesson_links:
                if url in progress['scraped_urls']: continue
                
                try:
                    r_l = session.get(url, timeout=20)
                    l_soup = BeautifulSoup(r_l.text, 'html.parser')
                    
                    title_el = l_soup.find('h1')
                    title = title_el.text.strip() if title_el else "No Title"
                    
                    # Loigiaihay uses #box-content or .box-content
                    content_el = l_soup.select_one('#box-content, .box-content, .detail_new, article')
                    if not content_el: continue
                    
                    # Clean up
                    for junk in content_el.select('.adsbygoogle, .social-share, script, style, .feedback'):
                        junk.decompose()
                        
                    content_md = md(str(content_el), heading_style="ATX").strip()
                    
                    success = storage.insert_article(
                        url=url,
                        title=title,
                        content=content_md,
                        metadata={"source": "loigiaihay.com", "subject_url": sub_url}
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
            print(f"  Error scanning subject {sub_url}: {e}")
            
    print(f"Finished Loigiaihay. Scraped {count} items.")
