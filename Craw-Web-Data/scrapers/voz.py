"""
VOZ Forum Scraper Plugin (Restored and Fixed)
"""
import os
import sys
import re
import time
import requests
from bs4 import BeautifulSoup
from markdownify import markdownify as md

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
}

from scrapers.storage_utils import get_storage

def process(source, global_settings):
    # Initialize SQLite Storage
    storage = get_storage(source['id'])
    
    print(f"Starting VOZ Forum crawler (Storage: SQLite)...")
    
    # Re-implemented parser with metadata cleanup
    def parse_thread(html):
        soup = BeautifulSoup(html, 'html.parser')
        posts = soup.select('article.message--post')
        extracted = []
        for post in posts:
            author = post.get('data-author', 'Unknown')
            content_elem = post.select_one('.message-body .bbWrapper')
            if content_elem:
                # Xenforo 2.3 cleanup
                for unwanted in content_elem.select('script, style, .js-selectToQuote, .u-srOnly, .message-fields'):
                    unwanted.decompose()
                
                content_html = str(content_elem)
                # Strip problematic JSON-like strings and base64 placeholders
                content_html = re.sub(r'\{"lightbox_.*?\}', '', content_html)
                content_html = re.sub(r'src="data:image/svg\+xml;base64,.*?"', '', content_html)
                
                content_md = md(content_html, heading_style="ATX").strip()
                # Final polish: strip common UI strings
                content_md = re.sub(r'"lightbox_.*?": ".*?",?', '', content_md)
                
                extracted.append(f"**{author}**:\n\n{content_md}")
        return "\n\n---\n\n".join(extracted)

    print("Re-created VOZ scraper with Xenforo 2.3 metadata cleanup.")
