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

    sitemap_index_url = "https://tinhte.vn/sitemap.xml"
    print(f"Fetching sitemap index from {sitemap_index_url}")
    
    headers = {
        "User-Agent": global_settings.get('user_agent', 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
        "Accept-Language": "vi-VN,vi;q=0.9,en-US;q=0.8,en;q=0.7"
    }
    
    try:
        r = requests.get(sitemap_index_url, headers=headers, timeout=30)
        if r.status_code != 200:
            print(f"Failed to fetch sitemap index: {r.status_code}")
            return
            
        index_soup = BeautifulSoup(r.text, 'xml')
        # Tinh tế has sitemap-1.xml, sitemap-2.xml etc.
        sitemap_urls = [loc.text for loc in index_soup.find_all('loc') if 'sitemap-' in loc.text]
        
        print(f"Found {len(sitemap_urls)} sub-sitemaps.")
        
        # Process sub-sitemaps (most recent first)
        for s_url in reversed(sitemap_urls):
            print(f"Processing sub-sitemap: {s_url}")
            sub_r = requests.get(s_url, headers=headers, timeout=30)
            if sub_r.status_code != 200:
                continue
                
            sub_soup = BeautifulSoup(sub_r.text, 'xml')
            urls = [loc.text for loc in sub_soup.find_all('loc') if '/thread/' in loc.text]
            
            count = 0
            for url in reversed(urls):
                if url in progress['scraped_urls']:
                    continue
                
                print(f"  Scraping thread: {url}")
                success = scrape_thread(url, storage, progress, global_settings, headers)
                if success:
                    count += 1
                    if count % 5 == 0:
                        print(f"  Progress: {count} threads scraped from this sitemap.")
                        with open(progress_file, 'w', encoding='utf-8') as f:
                            json.dump(progress, f, indent=2)
            
            # Save progress after each sub-sitemap
            with open(progress_file, 'w', encoding='utf-8') as f:
                json.dump(progress, f, indent=2)
                        
    except Exception as e:
        print(f"Error in Tinhte process: {e}")
        
    # Final save
    with open(progress_file, 'w', encoding='utf-8') as f:
        json.dump(progress, f, indent=2)

def scrape_thread(base_url, storage, progress, global_settings, headers):
    try:
        all_comments = []
        page = 1
        thread_title = ""
        
        while True:
            url = base_url if page == 1 else f"{base_url.rstrip('/')}/page-{page}"
            resp = requests.get(url, headers=headers, timeout=20)
            if resp.status_code != 200:
                break
                
            soup = BeautifulSoup(resp.text, 'html.parser')
            
            if page == 1:
                title_el = soup.select_one('.thread-title')
                thread_title = title_el.get_text(strip=True) if title_el else ""
                if not thread_title:
                    # Fallback for some views
                    title_el = soup.select_one('h1')
                    thread_title = title_el.get_text(strip=True) if title_el else ""

            # Find comment contents
            comment_els = soup.select('.thread-comment__content')
            if not comment_els:
                # Check for alternative version (standard XenForo)
                comment_els = soup.select('.message-inner .message-userContent')
            
            if not comment_els:
                break
                
            for el in comment_els:
                # Clean nested quotes if any (usually in blockquotes)
                # For training, keeping quotes might be okay, but let's clean typical UI junk
                for junk in el.select('script, style, .js-selectToQuote, .bbCodeBlock.bbCodeQuote, .bdPostTree_ParentPoster'):
                    junk.decompose()
                
                txt = el.get_text(separator="\n", strip=True)
                if txt:
                    all_comments.append(txt)
            
            # Check for next page
            # XenForo usually has a 'next' button or page numbers
            next_page_el = soup.select_one('a.pageNav-jump--next')
            if not next_page_el:
                break
            
            page += 1
            if page > 20: # Limit deep threads for now to avoid hang
                break
            
            time.sleep(global_settings.get('request_delay', 0.5))

        if not all_comments and not thread_title:
            return False

        # Construct final content from comments
        content_md = "\n\n---\n\n".join(all_comments)
        
        success = storage.insert_article(
            url=base_url,
            title=thread_title,
            author="Tinhte User",
            content=content_md,
            category="Forum",
            tags=["Tinh tế", "Comments"],
            metadata={"total_comments": len(all_comments), "pages_scraped": page}
        )
        
        if success:
            progress['scraped_urls'][base_url] = time.time()
            return True
            
        time.sleep(global_settings.get('request_delay', 0.5))
        
    except Exception as e:
        print(f"Failed to scrape {base_url}: {e}")
    return False
