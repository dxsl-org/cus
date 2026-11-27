import os
import re
import json
import time
import requests
from scrapers.storage_utils import get_storage

def process(source, global_settings):
    progress_file = source.get('progress_file', f"progress_{source.get('id')}.json")
    storage = get_storage(source['id'])
    
    progress = {}
    if os.path.exists(progress_file):
        try:
            with open(progress_file, 'r', encoding='utf-8') as f:
                progress = json.load(f)
        except:
            progress = {}
            
    if 'scraped_urls' not in progress:
        progress['scraped_urls'] = {}
    
    current_page = progress.get('current_page', 1)
    
    session = requests.Session()
    headers = {
        "User-Agent": global_settings.get('user_agent', "Mozilla/5.0"),
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.9,vi;q=0.8",
        "Referer": "https://spiderum.com/",
        "X-Requested-With": "XMLHttpRequest"
    }
    session.headers.update(headers)
    
    print(f"Starting Spiderum API Deep Scan (Page: {current_page})")
    
    while True:
        api_url = f"https://spiderum.com/api/v1/feed/getAllPosts?type=new&page={current_page}"
        try:
            r = session.get(api_url, timeout=30)
            if r.status_code != 200:
                print(f"API Error {r.status_code} at page {current_page}")
                break
                
            data = r.json()
            posts = data.get('posts', {}).get('items', [])
            
            if not posts:
                print(f"No more posts found at page {current_page}. Finishing.")
                break
                
            print(f"Found {len(posts)} posts on page {current_page}")
            
            for post in posts:
                slug = post.get('slug')
                if not slug: continue
                
                url = f"https://spiderum.com/bai-dang/{slug}"
                if url in progress['scraped_urls']:
                    continue
                
                scrape_article_session(slug, storage, progress, session, global_settings)
                
                # Periodic save
                if len(progress['scraped_urls']) % 10 == 0:
                    progress['current_page'] = current_page
                    with open(progress_file, 'w', encoding='utf-8') as f:
                        json.dump(progress, f, indent=2)
            
            current_page += 1
            progress['current_page'] = current_page
            
            # Rate limiting / Delay
            time.sleep(global_settings.get('request_delay', 1.0))
            
        except Exception as e:
            print(f"Error on page {current_page}: {e}")
            break

    # Final save
    with open(progress_file, 'w', encoding='utf-8') as f:
        json.dump(progress, f, indent=2)

def scrape_article_session(slug, storage, progress, session, global_settings):
    url = f"https://spiderum.com/bai-dang/{slug}"
    try:
        resp = session.get(url, timeout=30)
        if resp.status_code != 200:
            print(f"Error {resp.status_code} fetching HTML for {slug}")
            return
            
        html = resp.text
        # Find script with id serverApp-state
        script_match = re.search(r'<script id="serverApp-state" type="application/json">(.*?)</script>', html, re.DOTALL)
        if not script_match:
            print(f"Could not find state script for {slug}")
            return
            
        encoded_state = script_match.group(1)
        # Decode entities
        decoded = encoded_state.replace('&q;', '"').replace('&a;', '&').replace('&l;', '<').replace('&g;', '>')
        
        try:
            state = json.loads(decoded)
        except Exception as e:
            print(f"JSON Parse Error for {slug} state: {e}")
            return
            
        # Find the post key (it includes the slug)
        post_key = next((k for k in state.keys() if f'api/v1/post/{slug}' in k), None)
        if not post_key:
            # Fallback to any post key or detail key
            post_key = next((k for k in state.keys() if 'api/v1/post/' in k or 'getPostDetail' in k), None)
            
        if not post_key:
            print(f"Post data not found in state for {slug}. Keys: {list(state.keys())[:10]}")
            return
            
        entry = state[post_key]
        post_data = entry.get('post', entry) # Sometimes it's wrapped
        
        title = post_data.get('title', 'No Title')
        author = post_data.get('creator_id', {}).get('display_name', 'Unknown')
        created_at = post_data.get('created_at', '')
        category = post_data.get('cat_id', {}).get('name', '')
        
        # Reconstruct content from blocks
        content_md = ""
        body = post_data.get('body', {})
        blocks = body.get('blocks', [])
        
        for block in blocks:
            b_type = block.get('type')
            data = block.get('data', {})
            
            if b_type == 'paragraph':
                text = data.get('text', '')
                content_md += text + "\n\n"
            elif b_type == 'header':
                text = data.get('text', '')
                level = data.get('level', 2)
                content_md += "#" * level + " " + text + "\n\n"
            elif b_type == 'image':
                img_url = data.get('file', {}).get('url', '')
                caption = data.get('caption', '')
                if img_url:
                    content_md += f"![{caption}]({img_url})\n\n"
            elif b_type == 'list':
                items = data.get('items', [])
                style = data.get('style', 'unordered')
                for i, item in enumerate(items):
                    prefix = f"{i+1}." if style == 'ordered' else "-"
                    content_md += f"{prefix} {item}\n"
                content_md += "\n"
            elif b_type == 'quote':
                text = data.get('text', '')
                caption = data.get('caption', '')
                content_md += f"> {text}"
                if caption:
                    content_md += f"\n> -- {caption}"
                content_md += "\n\n"
            elif b_type == 'code':
                code = data.get('code', '')
                content_md += f"```\n{code}\n```\n\n"
                
        if not content_md.strip():
            # Fallback to content field if body.blocks is empty
            from markdownify import markdownify as md
            content_md = md(post_data.get('content', ''), heading_style="ATX")
            
        success = storage.insert_article(
            url=url,
            title=title,
            author=author,
            content=content_md,
            tags=[], # tags are often not in state or complex
            category=category,
            metadata={"created_at": created_at, "slug": slug}
        )
        
        if success:
            progress['scraped_urls'][url] = time.time()
            print(f"SUCCESS: {title[:50]}...")
            
    except Exception as e:
        print(f"Error scraping Spiderum article {slug}: {e}")
