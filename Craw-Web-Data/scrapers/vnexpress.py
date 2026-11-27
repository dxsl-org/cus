"""
VnExpress Scraper Plugin (API-based with Comments)
"""
import os
import json
import time
import requests
from bs4 import BeautifulSoup
from markdownify import markdownify as md
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
    if 'offsets' not in progress:
        progress['offsets'] = {}
    
    headers = {
        "User-Agent": global_settings.get('user_agent', "Mozilla/5.0")
    }
    
    # Core Categories for VnExpress (Site ID: 1000000)
    categories = {
        "Thời sự": "1001005",
        "Thế giới": "1001002",
        "Kinh doanh": "1003159",
        "Bất động sản": "1005621",
        "Khoa học": "1002591",
        "Giải trí": "1002691",
        "Thể thao": "1002565",
        "Pháp luật": "1001007",
        "Giáo dục": "1003497",
        "Sức khỏe": "1003750",
        "Đời sống": "1002966",
        "Du lịch": "1001006"
    }
    
    site_id = "1000000"
    limit = 50 
    
    print(f"Starting API-based Scan for {source['name']}")
    
    for cat_name, cat_id in categories.items():
        offset = progress['offsets'].get(cat_id, 0)
        print(f"Processing Category: {cat_name} (ID: {cat_id}) from offset {offset}")
        
        while True:
            api_url = f"https://gw.vnexpress.net/ar/get_rule_2?category_id={cat_id}&limit={limit}&offset={offset}&site_id={site_id}"
            try:
                r = requests.get(api_url, headers=headers, timeout=30)
                if r.status_code != 200:
                    break
                    
                resp_data = r.json()
                if not resp_data or 'data' not in resp_data or cat_id not in resp_data['data']:
                    break
                
                articles = resp_data['data'][cat_id].get('data', [])
                if not articles:
                    break
                
                for art in articles:
                    url = art.get('share_url')
                    if not url or url in progress['scraped_urls']:
                        continue
                    
                    article_id = art.get('article_id')
                    scrape_article(url, article_id, storage, progress, headers, global_settings)
                    
                    if len(progress['scraped_urls']) % 50 == 0:
                        save_progress(progress_file, progress, cat_id, offset)

                offset += limit
                save_progress(progress_file, progress, cat_id, offset)
                
            except Exception as e:
                print(f"Error in discovery loop: {e}")
                break

    save_progress(progress_file, progress)

def save_progress(path, progress, cat_id=None, offset=None):
    if cat_id and offset is not None:
        progress['offsets'][cat_id] = offset
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(progress, f, indent=2)

def scrape_article(url, article_id, storage, progress, headers, global_settings):
    try:
        resp = requests.get(url, headers=headers, timeout=30)
        if resp.status_code != 200:
            return
            
        soup = BeautifulSoup(resp.text, 'html.parser')
        title_el = soup.select_one('h1.title-detail')
        if not title_el: return
        
        title = title_el.get_text(strip=True)
        sapo_el = soup.select_one('p.description')
        sapo = sapo_el.get_text(strip=True) if sapo_el else ""
        
        content_el = soup.select_one('article.fck_detail')
        if not content_el: return

        # Extract Comments
        comments = []
        if article_id:
            try:
                comm_url = f"https://comment.vnexpress.net/api/get?objectid={article_id}&siteid=1000000&limit=100"
                cr = requests.get(comm_url, headers=headers, timeout=10)
                if cr.status_code == 200:
                    cdata = cr.json()
                    for cpost in cdata.get('data', {}).get('items', []):
                        content = cpost.get('content', '')
                        user = cpost.get('full_name', 'Anonymous')
                        comments.append(f"**{user}**: {content}")
            except:
                pass

        content_html = str(content_el)
        content_md = md(content_html, heading_style="ATX")
        if sapo: content_md = f"**{sapo}**\n\n{content_md}"
        
        if comments:
            content_md += "\n\n### Ý kiến độc giả\n" + "\n\n".join(comments)

        success = storage.insert_article(
            url=url,
            title=title,
            content=content_md,
            metadata={'article_id': article_id, 'sapo': sapo}
        )
        
        if success:
            progress['scraped_urls'][url] = time.time()
            print(f"SCRAPED: {title[:50]}")
        
        time.sleep(global_settings.get('request_delay', 0.5))
        
    except Exception as e:
        print(f"Error scraping {url}: {e}")
