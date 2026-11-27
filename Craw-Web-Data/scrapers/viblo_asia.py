"""
Viblo Scraper Plugin (SQLite Version)
"""
import os
import re
import time
import requests
from scrapers.storage_utils import get_storage

def process(source, global_settings):
    storage = get_storage(source['id'])
    print(f"Starting Viblo crawler (Storage: SQLite)...")
    
    # Viblo API-based discovery
    pass

def scrape_article(post_id, storage, progress, headers, global_settings):
    url = f"https://viblo.asia/p/{post_id}"
    try:
        # Viblo usually requires specific headers or uses a GraphQL/REST API
        api_url = f"https://api.viblo.asia/posts/{post_id}"
        resp = requests.get(api_url, headers=headers, timeout=30)
        if resp.status_code != 200:
            return
            
        data = resp.json().get('data', {})
        title = data.get('title', 'No Title')
        content_md = data.get('contents', '')
        author = data.get('user', {}).get('data', {}).get('name', 'Unknown')
        
        storage.insert_article(
            url=url,
            title=title,
            author=author,
            content=content_md,
            metadata={"source": "Viblo", "viblo_id": post_id}
        )
    except Exception as e:
        print(f"Error scraping Viblo article {post_id}: {e}")
