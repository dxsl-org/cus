"""
WordPress REST API Scraper Plugin
Targets WP-based sites like caphesach.info, nghiencuulichsu.com
"""
import os
import json
import time
import requests
from markdownify import markdownify as md

from scrapers.storage_utils import get_storage

def process(source, global_settings):
    base_url = source.get('url').rstrip('/')
    
    # Check if it's a wordpress.com site
    if "wordpress.com" in base_url and "public-api.wordpress.com" not in base_url:
        from urllib.parse import urlparse
        hostname = urlparse(base_url).hostname
        api_url = f"https://public-api.wordpress.com/wp/v2/sites/{hostname}/posts"
    else:
        api_url = f"{base_url}/wp-json/wp/v2/posts"
        
    progress_file = source.get('progress_file', f"progress_{source.get('id')}.json")
    
    # Initialize SQLite Storage
    storage = get_storage(source['id'])
    
    progress = {}
    if os.path.exists(progress_file):
        with open(progress_file, 'r', encoding='utf-8') as f:
            progress = json.load(f)
            
    headers = {
        "User-Agent": global_settings.get('user_agent', "Mozilla/5.0")
    }
    
    page = 1
    per_page = source.get('per_page', 100)
    scraped_count = 0
    total_posts = "Unknown"
    
    print(f"Starting WordPress API crawler for {source['name']} (Storage: SQLite) at {api_url}")
    
    while True:
        try:
            params = {
                "page": page,
                "per_page": per_page,
                "_embed": 1
            }
            resp = requests.get(api_url, params=params, headers=headers, timeout=30)
            
            # Check for total posts in headers
            if page == 1:
                total_posts = resp.headers.get('X-WP-Total', "Unknown")
                total_pages = resp.headers.get('X-WP-TotalPages', "Unknown")
                print(f"Total posts detected: {total_posts} across {total_pages} pages.")

            if resp.status_code == 400: # Assuming no more pages
                print(f"Reached end of pages at page {page}.")
                break
            
            if resp.status_code != 200:
                print(f"Error fetching page {page}: {resp.status_code}")
                # Try to see if it's just a temporary error or permanent
                if resp.status_code == 404: break # Page not found usually means end
                time.sleep(5)
                continue
                
            posts = resp.json()
            if not posts:
                print(f"No more posts found on page {page}.")
                break
                
            page_scraped = 0
            for post in posts:
                post_id = str(post['id'])
                if post_id in progress.get('scraped_ids', []):
                    continue
                
                title = post['title']['rendered']
                content_html = post['content']['rendered']
                url = post['link']
                
                content_md = md(content_html, heading_style="ATX")
                
                # Insert into SQLite
                success = storage.insert_article(
                    url=url,
                    title=title,
                    content=content_md,
                    metadata={
                        "wp_id": post.get('id'),
                        "date": post.get('date'),
                        "source": "WordPress API"
                    }
                )
                
                if success:
                    if 'scraped_ids' not in progress: progress['scraped_ids'] = []
                    progress['scraped_ids'].append(post_id)
                    scraped_count += 1
                    page_scraped += 1
                
            # Save progress after each page
            with open(progress_file, 'w', encoding='utf-8') as f:
                json.dump(progress, f, indent=2)
                
            print(f"Processed page {page}/{resp.headers.get('X-WP-TotalPages', '?')}, new articles: {page_scraped}, total scraped session: {scraped_count}")
            page += 1
            time.sleep(global_settings.get('request_delay', 0.5)) # Reduced delay for large syncs if safe
            
        except Exception as e:
            print(f"Exception on page {page}: {e}")
            time.sleep(5)
            # Optionally retry or break
            continue

    print(f"Finished WordPress API crawler for {source['name']}")
