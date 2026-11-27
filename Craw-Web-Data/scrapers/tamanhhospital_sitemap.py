"""
Tam Anh Hospital Sitemap-based Scraper Plugin
"""
import os
import sys
import json
import re
import time
from datetime import datetime
import requests
from bs4 import BeautifulSoup
from markdownify import markdownify as md

sys.stdout.reconfigure(encoding='utf-8')

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

def clean_html(soup):
    for s in soup.select('script, style, iframe, .ads, .social-share, .related-posts, .comment-respond, .contact-sidebar'):
        s.decompose()
    return soup

def download_image(url, filepath):
    return False

def extract_article(html, url):
    soup = BeautifulSoup(html, 'html.parser')
    
    # Prioritize the second post_info for full body
    content_els = soup.select('div.post_info.box_detail_post')
    content_el = content_els[-1] if len(content_els) >= 2 else (content_els[0] if content_els else None)
    
    # Fallbacks
    content_el = content_el or soup.select_one('#ftwp-postcontent') or soup.find('article') or soup.select_one('.entry-content')
    
    if not content_el:
        return None
        
    text_content = content_el.get_text(strip=True)
    if len(text_content) < 500 and not soup.select_one('.entry-content'):
        return None

    content_el = clean_html(content_el)
    title_el = soup.find('h1') or soup.select_one('.title h1')
    title = title_el.get_text(strip=True) if title_el else "No Title"
    
    content_md = md(str(content_el), heading_style="ATX").strip()
    return {'title': title, 'content': content_md, 'date': datetime.now().strftime('%Y-%m-%d')}

from scrapers.storage_utils import get_storage

def process(source, global_settings):
    # Initialize SQLite Storage
    storage = get_storage(source['id'])
    
    print(f"Starting Tam Anh Hospital crawler (Storage: SQLite)...")
    
    # Sitemap discovery logic would go here
    pass
