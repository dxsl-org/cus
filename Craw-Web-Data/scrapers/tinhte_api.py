"""
Tinhte Scraper Plugin (API-based via AppForo Batch)
"""
import os
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
            
    if 'scraped_threads' not in progress:
        progress['scraped_threads'] = {}
    if 'offsets' not in progress:
        progress['offsets'] = {}
    
    headers = {
        "User-Agent": global_settings.get('user_agent', "Mozilla/5.0")
    }
    
    # Priority Categories for Tinhte
    # 796: Điện thoại, 797: Máy tính, 798: Camera, 800: Xe, 799: Âm thanh
    categories = {
        "Điện thoại": "796",
        "Máy tính": "797",
        "Camera": "798",
        "Xe": "800",
        "Âm thanh": "799"
    }
    
    batch_url = "https://tinhte.vn/appforo/index.php?batch"
    limit = 20
    
    print(f"Starting AppForo Batch API Scan for {source['name']}")
    
    for cat_name, cat_id in categories.items():
        page = progress['offsets'].get(cat_id, 1)
        print(f"Processing Category: {cat_name} (ID: {cat_id}) from page {page}")
        
        while page < 50: # Limit depth for now
            # AppForo batch request payload
            payload = {
                "batch": json.dumps([
                    {
                        "method": "GET",
                        "uri": f"forums/{cat_id}/threads?page={page}&limit={limit}"
                    }
                ])
            }
            
            try:
                r = requests.post(batch_url, data=payload, headers=headers, timeout=30)
                if r.status_code != 200: break
                
                batch_data = r.json()
                if not batch_data or not isinstance(batch_data, list): break
                
                resp = batch_data[0]
                if resp.get('code') != 200: break
                
                threads = resp.get('body', {}).get('threads', [])
                if not threads: break
                
                print(f"Found {len(threads)} threads on page {page} of {cat_name}")
                
                for t in threads:
                    thread_id = t.get('thread_id')
                    if not thread_id or str(thread_id) in progress['scraped_threads']:
                        continue
                    
                    scrape_thread(thread_id, t, storage, progress, headers)
                    
                    if len(progress['scraped_threads']) % 20 == 0:
                        save_progress(progress_file, progress, cat_id, page)

                page += 1
                save_progress(progress_file, progress, cat_id, page)
                
            except Exception as e:
                print(f"Error in Tinhte discovery: {e}")
                break

    save_progress(progress_file, progress)

def save_progress(path, progress, cat_id=None, offset=None):
    if cat_id and offset is not None:
        progress['offsets'][cat_id] = offset
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(progress, f, indent=2)

def scrape_thread(thread_id, thread_meta, storage, progress, headers):
    try:
        batch_url = "https://tinhte.vn/appforo/index.php?batch"
        # Fetch posts for this thread
        payload = {
            "batch": json.dumps([
                {
                    "method": "GET",
                    "uri": f"threads/{thread_id}/posts?limit=50"
                }
            ])
        }
        
        r = requests.post(batch_url, data=payload, headers=headers, timeout=30)
        if r.status_code != 200: return
        
        data = r.json()
        if not data or not isinstance(data, list): return
        
        posts_body = data[0].get('body', {})
        posts = posts_body.get('posts', [])
        
        if not posts: return
        
        # Combine posts into a single conversation-style content
        full_content = []
        for p in posts:
            user = p.get('poster_username', 'User')
            text = p.get('post_body_plain_text', '')
            if text:
                full_content.append(f"**{user}**: {text}")
        
        content_md = "\n\n---\n\n".join(full_content)
        title = thread_meta.get('thread_title', 'Untitled Thread')
        
        success = storage.insert_article(
            url=f"https://tinhte.vn/thread/{thread_id}",
            title=title,
            content=content_md,
            metadata={'thread_id': thread_id, 'view_count': thread_meta.get('thread_view_count')}
        )
        
        if success:
            progress['scraped_threads'][str(thread_id)] = time.time()
            print(f"SCRAPED TinhTe: {title[:50]}")
            
    except Exception as e:
        print(f"Error scraping thread {thread_id}: {e}")
