import os
import json
import time
import re
import requests
import subprocess
from bs4 import BeautifulSoup
from markdownify import markdownify as md
from scrapers.storage_utils import get_storage

def get_cookies_via_playwright():
    """Run a small playwright script to get Cloudflare cookies."""
    print("Refreshing Cloudflare cookies via Playwright...")
    script_path = "get_nghiencuulichsu_cookies.py"
    try:
        # We reuse the existing get_nghiencuulichsu_cookies.py or create it if missing
        result = subprocess.run(["python", script_path], capture_output=True, text=True, timeout=120)
        if result.returncode == 0:
            with open("nghiencuulichsu_cookies.json", "r") as f:
                return json.load(f)
    except Exception as e:
        print(f"Failed to refresh cookies: {e}")
    return None

def get_headers(cookies):
    cookie_str = "; ".join([f"{c['name']}={c['value']}" for c in cookies])
    return {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Cookie": cookie_str,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8"
    }

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

    # Load initial cookies
    cookies = None
    if os.path.exists("nghiencuulichsu_cookies.json"):
        with open("nghiencuulichsu_cookies.json", "r") as f:
            cookies = json.load(f)
    else:
        cookies = get_cookies_via_playwright()

    if not cookies:
        print("CRITICAL: Failed to obtain cookies for Nghiên cứu Lịch sử.")
        return

    headers = get_headers(cookies)
    sitemap_url = "https://nghiencuulichsu.com/sitemap.xml"
    
    print(f"Starting Nghiên cứu Lịch sử scan from {sitemap_url}")
    
    try:
        r = requests.get(sitemap_url, headers=headers, timeout=30)
        if r.status_code == 403 or "Checking your browser" in r.text:
            print("Cookies expired. Refreshing...")
            cookies = get_cookies_via_playwright()
            if not cookies: return
            headers = get_headers(cookies)
            r = requests.get(sitemap_url, headers=headers, timeout=30)

        if r.status_code != 200:
            print(f"Failed to fetch sitemap: {r.status_code}")
            return

        soup = BeautifulSoup(r.text, 'xml')
        urls = [loc.text for loc in soup.find_all('loc')]
        
        print(f"Found {len(urls)} URLs in sitemap.")
        
        # Filter for article-like URLs: must have a date pattern /YYYY/MM/DD/
        date_pattern = re.compile(r'/\d{4}/\d{2}/\d{2}/')
        article_urls = [u for u in urls if date_pattern.search(u)]
        
        print(f"Processing {len(article_urls)} validated articles...")
        
        # Reverse to get newest first
        for url in reversed(article_urls):
            if url in progress['scraped_urls']:
                continue
            
            success = scrape_article(url, storage, progress, headers, global_settings)
            
            if not success:
                # Check if we were blocked mid-scan
                test_r = requests.get("https://nghiencuulichsu.com", headers=headers, timeout=10)
                if test_r.status_code == 403 or "Checking your browser" in test_r.text:
                    print("Mid-scan block detected. Refreshing cookies...")
                    cookies = get_cookies_via_playwright()
                    if not cookies: break
                    headers = get_headers(cookies)
                    # Retry once
                    scrape_article(url, storage, progress, headers, global_settings)

            # Periodic save
            if len(progress['scraped_urls']) % 10 == 0:
                with open(progress_file, 'w', encoding='utf-8') as f:
                    json.dump(progress, f, indent=2)
                    
    except Exception as e:
        print(f"Error in Nghiên cứu Lịch sử process: {e}")
        
    # Final save
    with open(progress_file, 'w', encoding='utf-8') as f:
        json.dump(progress, f, indent=2)

def scrape_article(url, storage, progress, headers, global_settings):
    try:
        resp = requests.get(url, headers=headers, timeout=30)
        if resp.status_code != 200:
            return False
            
        soup = BeautifulSoup(resp.text, 'html.parser')
        
        title_el = soup.select_one('article .entry-title')
        if not title_el:
            title_el = soup.select_one('h2.entry-title, h1.entry-title, h1')
            
        title = title_el.get_text(strip=True) if title_el else "No Title"
        
        # Some titles might have extra whitespace or "Nghiên Cứu Lịch Sử" prefix if not careful
        if title == "Nghiên Cứu Lịch Sử" and soup.select_one('h1'):
            title = soup.select_one('h1').get_text(strip=True)
        
        content_el = soup.select_one('.entry-content')
        if not content_el:
            print(f"Skipping {url}: No content element found.")
            return False

        content_md = md(str(content_el), heading_style="ATX")
        
        date = ""
        date_meta = soup.find('meta', property='article:published_time')
        if date_meta:
            date = date_meta.get('content')
        else:
            date_el = soup.select_one('time.entry-date')
            if date_el:
                date = date_el.get('datetime')
        
        author = "Unknown"
        # Often the actual author name is at the top of content in emphasis/bold
        # We try to extract it from meta first
        author_meta = soup.select_one(".entry-meta .author a")
        if author_meta:
            author = author_meta.get_text(strip=True)
            
        category = ""
        cat_el = soup.select_one(".cat-links a")
        if cat_el:
            category = cat_el.get_text(strip=True)

        tags = [a.get_text(strip=True) for a in soup.select(".tag-links a, a[rel='tag']")]

        success = storage.insert_article(
            url=url,
            title=title,
            author=author,
            content=content_md,
            category=category,
            tags=tags,
            metadata={"published_time": date}
        )
        
        if success:
            progress['scraped_urls'][url] = time.time()
            print(f"SUCCESS: {title[:50]}...")
            return True
        
        time.sleep(global_settings.get('request_delay', 2.0)) # Higher delay for sensitive sites
        
    except Exception as e:
        print(f"Failed to scrape {url}: {e}")
    return False
