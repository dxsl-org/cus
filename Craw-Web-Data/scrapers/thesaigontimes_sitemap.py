"""
The Saigon Times Sitemap-based Scraper Plugin
Uses the Yoast SEO sitemap index at thesaigontimes.vn/sitemap_index.xml to discover all article URLs,
then fetches each article with requests and extracts content with BeautifulSoup.

Strategy:
1. Parse sitemap index -> get all post-sitemap*.xml URLs
2. Parse each post sitemap -> get all article URLs
3. For each article URL, fetch HTML and extract:
   - Title: h1.tdb-title-text
   - Sapo: First <p> in content
   - Content: .tdb-article-content .tdb-block-inner
   - Author / Date / Category: from JSON-LD or elements
4. Save as Markdown with frontmatter and download images
"""
import os
import glob
import sys
import json
import re
import time
import random
from datetime import datetime
from urllib.parse import urlparse
import requests
from bs4 import BeautifulSoup
from markdownify import markdownify as md

sys.stdout.reconfigure(encoding='utf-8')

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

def sanitize_filename(text):
    text = re.sub(r'[\\/:\"*?<>|]', '-', text)
    text = re.sub(r'\s+', '_', text)
    return text[:200]

def fetch_xml(url, retries=3, timeout=30):
    for attempt in range(retries):
        try:
            resp = requests.get(url, headers=HEADERS, timeout=timeout)
            resp.raise_for_status()
            return BeautifulSoup(resp.content, 'xml')
        except Exception as e:
            if attempt < retries - 1:
                time.sleep(3)
            else:
                print(f"  Failed to fetch {url}: {e}")
                return None

def get_post_sitemaps(sitemap_url):
    print(f"Fetching sitemap index: {sitemap_url}")
    index_soup = fetch_xml(sitemap_url)
    if not index_soup:
        return []
    
    post_sitemaps = []
    for sitemap in index_soup.find_all('sitemap'):
        loc = sitemap.find('loc')
        if loc and '/post-sitemap' in loc.text:
            post_sitemaps.append(loc.text)
            
    try:
        post_sitemaps.sort(key=lambda x: int(re.search(r'post-sitemap(\d+)', x).group(1)), reverse=True)
    except:
        post_sitemaps.sort(reverse=True)
        
    print(f"Found {len(post_sitemaps)} post sitemaps.")
    return post_sitemaps

def get_urls_from_sitemap(sitemap_url):
    soup = fetch_xml(sitemap_url)
    if not soup:
        return []
    
    urls = []
    for url_tag in soup.find_all('url'):
        loc = url_tag.find('loc')
        if loc:
            article_url = loc.text.strip()
            if article_url != sitemap_url:
                date_tag = url_tag.find('lastmod')
                lastmod = date_tag.text if date_tag else ""
                urls.append({"url": article_url, "lastmod": lastmod})
    return urls

def extract_article(html, url):
    soup = BeautifulSoup(html, 'html.parser')
    title_el = soup.select_one('h1.tdb-title-text') or soup.select_one('h1.entry-title') or soup.find('h1')
    title = title_el.get_text(strip=True) if title_el else ''
    
    date_formatted = datetime.now().strftime('%Y-%m-%d')
    author = "The Saigon Times"
    category = "Kinh tế"

    content_el = soup.select_one('.tdb-article-content .tdb-block-inner') or soup.select_one('.td-post-content')
    content_md = ""
    images = []
    
    if content_el:
        for unwanted in content_el.select('script, style, iframe, .td-related-title'):
            unwanted.decompose()
        for img in content_el.find_all('img'):
            src = img.get('src') or img.get('data-src')
            if src and src.startswith('http'):
                images.append(src)
        content_md = md(str(content_el), heading_style="ATX")
        
    return {
        'title': title,
        'category': category,
        'content': content_md,
        'author': author,
        'date': date_formatted,
        'images': images,
        'url': url
    }

def url_to_slug(url):
    return url.strip('/').split('/')[-1]

from scrapers.storage_utils import get_storage

def process(source, global_settings):
    base_url = "https://thesaigontimes.vn"
    progress_file = source.get('progress_file', f"progress_{source['id']}.json")
    sitemap_url = source.get('sitemap_url', 'https://thesaigontimes.vn/sitemap_index.xml')
    
    # Initialize SQLite Storage
    storage = get_storage(source['id'])
    
    processed_urls = set()
    if os.path.exists(progress_file):
        try:
            with open(progress_file, 'r', encoding='utf-8') as f:
                progress = json.load(f)
                processed_urls = set(progress.get('processed_urls', []))
        except: pass
            
    delay = global_settings.get('delay_between_requests_seconds', 2.0)
    timeout = global_settings.get('request_timeout_seconds', 30)
    
    post_sitemaps = get_post_sitemaps(sitemap_url)
    total_articles = 0
    
    print(f"Starting The Saigon Times crawler (Storage: SQLite)...")
    
    for sm_url in post_sitemaps:
        articles = get_urls_from_sitemap(sm_url)
        for art_info in articles:
            art_url = art_info['url']
            if art_url in processed_urls: continue
                
            max_retries = 3
            for attempt in range(max_retries):
                try:
                    resp = requests.get(art_url, headers=HEADERS, timeout=timeout)
                    if resp.status_code == 503:
                        wait = (attempt + 1) * 10
                        time.sleep(wait)
                        continue
                    resp.raise_for_status()
                    resp.encoding = 'utf-8'
                    
                    article = extract_article(resp.text, art_url)
                    
                    # Insert into SQLite
                    success = storage.insert_article(
                        url=art_url,
                        title=article['title'],
                        author=article['author'],
                        content=article['content'],
                        category=article['category'],
                        metadata={
                            "date": article['date'],
                            "images": article['images']
                        }
                    )
                    
                    if success:
                        total_articles += 1
                        if total_articles % 10 == 0: print(f"✍️ {total_articles} articles...")
                    break
                except Exception as e:
                    print(f"Error fetching {art_url}: {e}")
                    break
                    
            processed_urls.add(art_url)
            time.sleep(delay + random.uniform(0, 1.0))
            
        with open(progress_file, 'w', encoding='utf-8') as f:
            json.dump({'processed_urls': list(processed_urls)}, f)
