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

    sitemap_url = "http://hoidisanvanhoa.vn/post-sitemap.xml"
    print(f"Fetching sitemap from {sitemap_url}")
    
    headers = {
        "User-Agent": global_settings.get('user_agent', 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36')
    }
    
    try:
        r = requests.get(sitemap_url, headers=headers, timeout=30)
        if r.status_code != 200:
            print(f"Failed to fetch sitemap: {r.status_code}")
            return
            
        soup = BeautifulSoup(r.text, 'xml')
        article_urls = [loc.text for loc in soup.find_all('loc')]
        
        print(f"Found {len(article_urls)} articles in sitemap.")
        
        count = 0
        for url in reversed(article_urls):
            if url in progress['scraped_urls']:
                continue
            
            success = scrape_article(url, storage, progress, global_settings, headers)
            if success:
                count += 1
                if count % 10 == 0:
                    print(f"  Progress: {count} articles scraped.")
                    with open(progress_file, 'w', encoding='utf-8') as f:
                        json.dump(progress, f, indent=2)
                        
    except Exception as e:
        print(f"Error in Hoidisan process: {e}")
        
    # Final save
    with open(progress_file, 'w', encoding='utf-8') as f:
        json.dump(progress, f, indent=2)

def scrape_article(url, storage, progress, global_settings, headers):
    try:
        resp = requests.get(url, headers=headers, timeout=20)
        if resp.status_code != 200:
            return False
            
        soup = BeautifulSoup(resp.text, 'html.parser')
        
        title_el = soup.select_one('h1.entry-title')
        title = title_el.get_text(strip=True) if title_el else ""
        
        content_el = soup.select_one('.entry-content')
        if not content_el:
            return False
            
        # Sapo: look for .artcl-sapo or the first bold paragraph
        sapo_el = soup.select_one('.artcl-sapo')
        if not sapo_el:
            p_first = content_el.select_one('p')
            if p_first and (p_first.select_one('strong') or p_first.select_one('b')):
                sapo_el = p_first
        
        sapo = sapo_el.get_text(strip=True) if sapo_el else ""
        
        # Clean content
        for el in content_el.select('.ads, .banner, script, style, .sharedaddy'):
            el.decompose()
            
        # Convert to markdown
        content_md = ""
        if sapo:
            content_md += f"> {sapo}\n\n"
        content_md += md(str(content_el), heading_style="ATX")
        
        # Metadata
        date_el = soup.select_one('meta[property="article:published_time"]')
        date_str = date_el['content'] if date_el and date_el.has_attr('content') else ""
        
        author_el = soup.select_one('meta[name="author"]')
        author = author_el['content'] if author_el and author_el.has_attr('content') else "Hội Di sản"

        category = ""
        cat_meta = soup.select_one('meta[property="article:section"]')
        if cat_meta:
            category = cat_meta['content']

        tags = [a.get_text(strip=True) for a in soup.select('.tags-links a')]

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
