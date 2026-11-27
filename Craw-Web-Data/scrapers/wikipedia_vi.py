"""
Wikipedia Tiếng Việt Scraper Plugin
"""
import os
import json
import time
import requests

from scrapers.storage_utils import get_storage

def process(source, global_settings):
    base_url = "https://vi.wikipedia.org/w/api.php"
    storage = get_storage(source['id'])
    headers = {"User-Agent": global_settings.get('user_agent', "Mozilla/5.0")}
    
    categories = ["Lịch sử Việt Nam", "Văn hóa Việt Nam", "Địa lý Việt Nam"]
    scraped_count = 0
    limit = 1000

    for cat in categories:
        if scraped_count >= limit: break
        print(f"Scanning category: {cat}")
        
        # Category members
        params = {
            "action": "query",
            "format": "json",
            "list": "categorymembers",
            "cmtitle": f"Category:{cat}",
            "cmlimit": 500
        }
        
        try:
            resp = requests.get(base_url, params=params, headers=headers).json()
            members = resp.get('query', {}).get('categorymembers', [])
            
            for member in members:
                if scraped_count >= limit: break
                if member['ns'] != 0: continue # Only articles
                
                title = member['title']
                url = f"https://vi.wikipedia.org/wiki/{title.replace(' ', '_')}"
                
                # Get content
                p_params = {
                    "action": "parse",
                    "format": "json",
                    "page": title,
                    "prop": "text"
                }
                p_resp = requests.get(base_url, params=p_params, headers=headers).json()
                html = p_resp.get('parse', {}).get('text', {}).get('*', "")
                
                if not html: continue
                
                from markdownify import markdownify as md
                content_md = md(html)
                
                if storage.insert_article(url=url, title=title, content=content_md):
                    scraped_count += 1
                    print(f"  [{scraped_count}] Scraped: {title}")
                
                time.sleep(global_settings.get('request_delay', 0.5))
                
        except Exception as e:
            print(f"Error in Wikipedia scraper for {cat}: {e}")
