import os
import json
import time
from curl_cffi import requests
from bs4 import BeautifulSoup
from markdownify import markdownify as md
from urllib.parse import urljoin
from scrapers.storage_utils import get_storage

def get_headers(global_settings):
    return {
        "User-Agent": global_settings.get('user_agent', "Mozilla/5.0"),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
    }

def process(source, global_settings):
    storage = get_storage(source['id'])
    progress_file = source.get('progress_file', f"progress_{source.get('id')}.json")
    
    progress = {}
    if os.path.exists(progress_file):
        try:
            with open(progress_file, 'r', encoding='utf-8') as f:
                progress = json.load(f)
        except: pass
            
    if 'scraped_urls' not in progress: progress['scraped_urls'] = {}
    if 'scanned_issues' not in progress: progress['scanned_issues'] = []
    
    print("Starting VJOL crawler (Portal-wide OJS)")
    session = requests.Session(impersonate="chrome110", verify=False)
    
    try:
        print("Fetching VJOL homepage for journals...")
        r = session.get("https://vjol.info.vn/", headers=get_headers(global_settings), timeout=30)
        soup = BeautifulSoup(r.text, 'html.parser')
        
        journal_links = []
        for a in soup.select('h3 a, .journal-title a, .journalLink'):
            href = a.get('href')
            if href and '/index.php/' in href:
                journal_links.append(urljoin("https://vjol.info.vn/", href))
                
        journal_links = list(set(journal_links))
        print(f"Found {len(journal_links)} journals.")
        
        count = 0
        for j_url in journal_links:
            print(f"Scanning journal: {j_url}")
            
            article_links = set()
            issue_links = set()
            
            # 1. Check archive
            archive_url = j_url.rstrip('/') + '/issue/archive'
            try:
                r_arc = session.get(archive_url, headers=get_headers(global_settings), timeout=15)
                arc_soup = BeautifulSoup(r_arc.text, 'html.parser')
                for a in arc_soup.find_all('a', href=True):
                    if 'issue/view' in a['href']: issue_links.add(urljoin(r_arc.url, a['href']))
            except: pass
            
            # 2. Check current issue
            current_url = j_url.rstrip('/') + '/issue/current'
            try:
                r_cur = session.get(current_url, headers=get_headers(global_settings), timeout=15)
                cur_soup = BeautifulSoup(r_cur.text, 'html.parser')
                for a in cur_soup.find_all('a', href=True):
                    if 'article/view' in a['href']: article_links.add(urljoin(r_cur.url, a['href']))
            except: pass
            
            # 3. Process collected issues
            for issue_url in list(issue_links):
                if issue_url in progress['scanned_issues']: continue
                
                try:
                    r_iss = session.get(issue_url, headers=get_headers(global_settings), timeout=15)
                    iss_soup = BeautifulSoup(r_iss.text, 'html.parser')
                    for a in iss_soup.find_all('a', href=True):
                        if 'article/view' in a['href']:
                            article_links.add(urljoin(r_iss.url, a['href']))
                            
                    progress['scanned_issues'].append(issue_url)
                except: pass
                
            # Process articles
            for a_link in list(article_links):
                # Clean up URL (remove /pdf or /fulltext etc.)
                a_link = a_link.split('/issue/')[0].split('/download/')[0] 
                
                if a_link in progress['scraped_urls']: continue
                
                success = scrape_article(session, a_link, storage, global_settings)
                if success:
                    count += 1
                    progress['scraped_urls'][a_link] = time.time()
                    
                with open(progress_file, 'w', encoding='utf-8') as f:
                    json.dump(progress, f, indent=2)
                    
                time.sleep(global_settings.get('request_delay', 0.5))
                
            print(f"Scraped {count} new VJOL articles so far.")
            
    except Exception as e:
        print(f"Error in VJOL process: {e}")

def scrape_article(session, url, storage, global_settings):
    try:
        r = session.get(url, headers=get_headers(global_settings), timeout=15)
        if r.status_code != 200: return False
            
        soup = BeautifulSoup(r.text, 'html.parser')
        
        title_el = soup.select_one('h1.page_title, .article_details h1, h1')
        title = title_el.get_text(strip=True) if title_el else ""
        if not title: return False
        
        authors = [a.get_text(strip=True) for a in soup.select('.authors .name, .author_name')]
        
        abstract_el = soup.select_one('.abstract, .article_abstract, .item.abstract')
        if not abstract_el: return False
        
        content_md = md(str(abstract_el), heading_style="ATX").strip()
        
        date_el = soup.select_one('.published .value, .date-published')
        date_str = date_el.get_text(strip=True) if date_el else ""
        
        success = storage.insert_article(
            url=url,
            title=title,
            author=", ".join(authors),
            content=content_md,
            metadata={"source": "vjol.info.vn", "publish_date": date_str}
        )
        return success
    except Exception as e:
        pass
    return False
