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

    sitemap_index_url = "https://genk.vn/sitemap.xml"
    print(f"Fetching sitemap index from {sitemap_index_url}")
    
    headers = {
        "User-Agent": global_settings.get('user_agent', 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36')
    }
    
    try:
        r = requests.get(sitemap_index_url, headers=headers, timeout=30)
        if r.status_code != 200:
            print(f"Failed to fetch sitemap index: {r.status_code}")
            return
            
        index_soup = BeautifulSoup(r.text, 'xml')
        sitemap_urls = [loc.text for loc in index_soup.find_all('loc') if 'StaticSitemaps' in loc.text]
        
        # Add core sitemaps too
        core_sitemaps = [loc.text for loc in index_soup.find_all('loc') if 'latestnews' in loc.text or 'google-news' in loc.text]
        sitemap_urls = core_sitemaps + sitemap_urls
        
        print(f"Found {len(sitemap_urls)} sub-sitemaps.")
        
        # Process sitemaps
        for s_url in sitemap_urls:
            print(f"Processing sub-sitemap: {s_url}")
            sub_r = requests.get(s_url, headers=headers, timeout=30)
            if sub_r.status_code != 200:
                continue
                
            sub_soup = BeautifulSoup(sub_r.text, 'xml')
            article_urls = [loc.text for loc in sub_soup.find_all('loc')]
            
            count = 0
            for url in reversed(article_urls):
                if url in progress['scraped_urls']:
                    continue
                
                success = scrape_article(url, storage, progress, global_settings, headers)
                if success:
                    count += 1
                    if count % 10 == 0:
                        print(f"  Progress: {count} articles scraped from this sitemap.")
                        with open(progress_file, 'w', encoding='utf-8') as f:
                            json.dump(progress, f, indent=2)
            
            # Save after each sub-sitemap
            with open(progress_file, 'w', encoding='utf-8') as f:
                json.dump(progress, f, indent=2)
                        
    except Exception as e:
        print(f"Error in GenK process: {e}")
        
    # Final save
    with open(progress_file, 'w', encoding='utf-8') as f:
        json.dump(progress, f, indent=2)

def scrape_article(url, storage, progress, global_settings, headers):
    try:
        resp = requests.get(url, headers=headers, timeout=20)
        if resp.status_code != 200:
            return False
            
        soup = BeautifulSoup(resp.text, 'html.parser')
        
        title_el = soup.select_one('h1.kbwc-title')
        title = title_el.get_text(strip=True) if title_el else ""
        
        content_el = soup.select_one('.knc-content')
        if not content_el:
            return False
            
        sapo_el = soup.select_one('.knc-sapo')
        sapo = sapo_el.get_text(strip=True) if sapo_el else ""
        
        # Clean content
        for el in content_el.select('.link-source-wrapper, .adm-mainsection, script, style'):
            el.decompose()
            
        # Convert to markdown
        content_md = ""
        if sapo:
            content_md += f"> {sapo}\n\n"
        content_md += md(str(content_el), heading_style="ATX")
        
        # Metadata
        date_el = soup.select_one('span.kbwcm-time')
        date_str = date_el['title'] if date_el and date_el.has_attr('title') else ""
        
        author_el = soup.select_one('.kbwcm-author')
        author = author_el.get_text(strip=True).split(',')[0] if author_el else "GenK"

        category = ""
        cat_el = soup.select_one('.gbrcwli.active a')
        if cat_el:
            category = cat_el.get_text(strip=True)

        tags = [a.get_text(strip=True) for a in soup.select('.knc-relate-tag a')]

        success = storage.insert_article(
            url=url,
            title=title,
            author=author,
            content=content_md,
            category=category,
            tags=tags,
            metadata={"publish_date": date_str, "sapo": sapo}
        )
        
        if success:
            progress['scraped_urls'][url] = time.time()
            return True
            
        time.sleep(global_settings.get('request_delay', 0.5))
        
    except Exception as e:
        print(f"Failed to scrape {url}: {e}")
    return False
