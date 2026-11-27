import os
import json
import time
from bs4 import BeautifulSoup
from curl_cffi import requests
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
            pass
            
    if 'scraped_urls' not in progress:
        progress['scraped_urls'] = {}

    print(f"Starting Lazi crawler (Recursive link discovery)")
    session = requests.Session(impersonate="chrome110")
    
    # We will use a queue to discover links recursively
    queue = ["https://lazi.vn/"]
    visited = set()
    
    try:
        count = 0
        while queue and count < 1000: # limit to avoid infinite loop
            url = queue.pop(0)
            if url in visited:
                continue
            visited.add(url)
            
            print(f"  Fetching: {url}")
            r = session.get(url, timeout=20)
            if r.status_code != 200:
                continue
                
            soup = BeautifulSoup(r.text, 'html.parser')
            
            # Find all question links on this page
            links = []
            for a in soup.select('a'):
                href = a.get('href')
                if href and ('/edu/exercise/' in href or '/bai-tap/' in href):
                    if href.startswith('/'):
                        href = 'https://lazi.vn' + href
                    links.append(href)
            
            links = list(set(links))
            
            # Add new links to queue
            for l in links:
                if l not in visited and l not in progress['scraped_urls']:
                    queue.append(l)
                    
            # If this page itself is a question page, scrape it
            if '/edu/exercise/' in url or '/bai-tap/' in url:
                if url not in progress['scraped_urls']:
                    success = scrape_question(soup, url, storage, progress)
                    if success:
                        count += 1
                        with open(progress_file, 'w', encoding='utf-8') as f:
                            json.dump(progress, f, indent=2)
            
            time.sleep(global_settings.get('request_delay', 1.5))
            
    except Exception as e:
        print(f"Error in Lazi process: {e}")

def scrape_question(soup, url, storage, progress):
    try:
        title_el = soup.find('h1')
        title = title_el.get_text(strip=True) if title_el else ""
        if not title:
            return False
            
        # The main question content is often a sibling of H1 or inside a specific wrapper
        # The answers are usually in .lz-view_answer-content or .box-reply
        content_el = soup.find('div', class_='lz-question--content') or soup.find('div', class_='box-question-content')
        answers_el = soup.find('div', class_='lz-view_answer-content') or soup.find('div', class_='box-reply')
        
        full_content = ""
        if content_el:
            # remove scripts and hidden elements
            for el in content_el.select('script, style, .hidden'):
                el.decompose()
            full_content += "**CÂU HỎI:**\n" + md(str(content_el), heading_style="ATX").strip() + "\n\n"
            
        if answers_el:
            for el in answers_el.select('script, style, .hidden, .qc'):
                el.decompose()
            full_content += "---\n**TRẢ LỜI:**\n" + md(str(answers_el), heading_style="ATX").strip()
            
        if not full_content:
            return False
            
        success = storage.insert_article(
            url=url,
            title=title,
            content=full_content,
            metadata={"source": "lazi.vn"}
        )
        
        if success:
            progress['scraped_urls'][url] = time.time()
            return True

    except Exception as e:
        print(f"    Failed to scrape {url}: {e}")
    return False
