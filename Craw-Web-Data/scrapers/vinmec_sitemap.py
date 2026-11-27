"""
Vinmec Sitemap Scraper Plugin (SQLite Version)
"""
import os
import re
import time
import requests
from bs4 import BeautifulSoup
from markdownify import markdownify as md
from scrapers.storage_utils import get_storage

def process(source, global_settings):
    storage = get_storage(source['id'])
    print(f"Starting Vinmec crawler (Storage: SQLite)...")
    
    # Vinmec sitemap discovery
    pass

def scrape_article(url, storage, progress, headers, global_settings):
    try:
        resp = requests.get(url, headers=headers, timeout=30)
        if resp.status_code != 200:
            return
            
        soup = BeautifulSoup(resp.text, 'html.parser')
        title = soup.select_one('h1').text.strip() if soup.select_one('h1') else "No Title"
        content_el = soup.select_one('.article-content') or soup.select_one('.section-content')
        
        if not content_el:
            return
            
        content_md = md(str(content_el), heading_style="ATX")
        storage.insert_article(
            url=url,
            title=title,
            content=content_md,
            metadata={"source": "Vinmec"}
        )
    except Exception as e:
        print(f"Error scraping Vinmec article {url}: {e}")
