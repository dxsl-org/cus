"""
Webtretho Scraper Plugin (GraphQL API)
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
            
    if 'scraped_posts' not in progress:
        progress['scraped_posts'] = {}
    if 'cursors' not in progress:
        progress['cursors'] = {}
    
    headers = {
        "User-Agent": global_settings.get('user_agent', "Mozilla/5.0"),
        "Content-Type": "application/json"
    }
    
    # Major Communities on Webtretho
    # slugs: lam-me, tam-su, dep, an-ngon, chuyen-gia-tu-van
    communities = {
        "Làm mẹ": "lam-me",
        "Tâm sự": "tam-su",
        "Đẹp": "dep",
        "Ăn ngon": "an-ngon",
        "Kinh nghiệm hay": "kinh-nghiem-hay"
    }
    
    api_url = "https://www.webtretho.vn/api"
    
    print(f"Starting GraphQL API Scan for {source['name']}")
    
    for comm_name, comm_slug in communities.items():
        cursor = progress['cursors'].get(comm_slug, "")
        print(f"Processing Community: {comm_name} (Slug: {comm_slug}) from cursor {cursor}")
        
        for _ in range(20): # Depth limit
            query = """
            query getCommunityThread($slug: String!, $cursor: String, $limit: Int) {
              community(slug: $slug) {
                threads(cursor: $cursor, limit: $limit) {
                  items {
                    id
                    title
                    slug
                    firstPost {
                      id
                      content
                      author { nickname }
                    }
                  }
                  cursor
                }
              }
            }
            """
            variables = {
                "slug": comm_slug,
                "cursor": cursor,
                "limit": 20
            }
            
            try:
                r = requests.post(api_url, json={"query": query, "variables": variables}, headers=headers, timeout=30)
                if r.status_code != 200: break
                
                data = r.json()
                thread_data = data.get('data', {}).get('community', {}).get('threads', {})
                items = thread_data.get('items', [])
                if not items: break
                
                for item in items:
                    post_id = item.get('id')
                    if not post_id or str(post_id) in progress['scraped_posts']:
                        continue
                    
                    # For Webtretho, the first post often contains the main content
                    first_post = item.get('firstPost', {})
                    content = first_post.get('content', '')
                    title = item.get('title', 'Untitled')
                    author = first_post.get('author', {}).get('nickname', 'Anonymous')
                    
                    if not content: continue
                    
                    # We could fetch replies here too, but start with main posts
                    success = storage.insert_article(
                        url=f"https://www.webtretho.com/f/{comm_slug}/{item.get('slug')}",
                        title=title,
                        content=f"**{author}**: {content}",
                        author=author,
                        metadata={'post_id': post_id, 'community': comm_slug}
                    )
                    
                    if success:
                        progress['scraped_posts'][str(post_id)] = time.time()
                        print(f"SCRAPED Webtretho: {title[:50]}")

                cursor = thread_data.get('cursor')
                if not cursor: break
                
                # Save progress
                progress['cursors'][comm_slug] = cursor
                with open(progress_file, 'w', encoding='utf-8') as f:
                    json.dump(progress, f, indent=2)
                    
                time.sleep(1)
                
            except Exception as e:
                print(f"Error in Webtretho GraphQL loop: {e}")
                break

    # Final save
    with open(progress_file, 'w', encoding='utf-8') as f:
        json.dump(progress, f, indent=2)
