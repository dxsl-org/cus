import os
import json
import time
import requests
import re
from bs4 import BeautifulSoup
from markdownify import markdownify as md
from scrapers.storage_utils import get_storage

CATEGORIES = [
    {"name": "Tiêu điểm", "url": "https://www.tapchicongsan.org.vn/web/guest/tieu-diem"},
    {"name": "Chính trị - Xây dựng Đảng", "url": "https://www.tapchicongsan.org.vn/web/guest/chinh-tri-xay-dung-dang"},
    {"name": "Kinh tế", "url": "https://www.tapchicongsan.org.vn/web/guest/kinh-te"},
    {"name": "Văn hóa - Xã hội", "url": "https://www.tapchicongsan.org.vn/web/guest/van_hoa_xa_hoi"},
    {"name": "Quốc phòng - An ninh - Đối ngoại", "url": "https://www.tapchicongsan.org.vn/web/guest/quoc-phong-an-ninh-oi-ngoai1"},
    {"name": "Thế giới: Vấn đề - Sự kiện", "url": "https://www.tapchicongsan.org.vn/web/guest/the-gioi-van-de-su-kien"},
    {"name": "Thực tiễn - Kinh nghiệm", "url": "https://www.tapchicongsan.org.vn/web/guest/thuc-tien-kinh-nghiem1"},
    {"name": "Nghiên cứu - Trao đổi", "url": "https://www.tapchicongsan.org.vn/web/guest/nghien-cu"},
    {"name": "Thông tin lý luận", "url": "https://www.tapchicongsan.org.vn/web/guest/thong-tin-ly-luan"}
]

def process(source, global_settings):
    storage = get_storage(source['id'])
    progress_file = source.get('progress_file', f"progress_{source.get('id')}.json")
    
    progress = {}
    if os.path.exists(progress_file):
        try:
            with open(progress_file, 'r', encoding='utf-8') as f:
                progress = json.load(f)
        except:
            progress = {}
            
    if 'scraped_urls' not in progress:
        progress['scraped_urls'] = {}

    headers = {
        "User-Agent": global_settings.get('user_agent', 'Mozilla/5.0')
    }

    print(f"Starting Tạp chí Cộng Sản crawler (Category-based)")
    
    try:
        for cat in CATEGORIES:
            print(f"Crawling category: {cat['name']}")
            # Crawl through available pages
            for page in range(1, 501):
                page_url = cat['url']
                if page > 1:
                    page_url += f"?_contentpublisher_WAR_viettelcmsportlet_cur={page}"
                
                print(f"  Fetching page {page}: {page_url}")
                r = requests.get(page_url, headers=headers, timeout=30)
                if r.status_code != 200:
                    break
                    
                soup = BeautifulSoup(r.text, 'html.parser')
                # Find article links
                # Updated selector: .titleNews a
                links = soup.select('.titleNews a')
                article_urls = []
                for link in links:
                    href = link.get('href')
                    if href and 'tapchicongsan.org.vn' in href:
                        article_urls.append(href)
                
                article_urls = list(set(article_urls))
                if not article_urls:
                    print("    No more articles found in this category.")
                    break
                    
                count = 0
                for url in article_urls:
                    if url in progress['scraped_urls']:
                        continue
                        
                    success = scrape_article(url, storage, progress, global_settings)
                    if success:
                        count += 1
                
                print(f"    Scraped {count} new articles from page {page}.")
                with open(progress_file, 'w', encoding='utf-8') as f:
                    json.dump(progress, f, indent=2)
                
                time.sleep(global_settings.get('request_delay', 1))

    except Exception as e:
        print(f"Error in Tạp chí Cộng Sản process: {e}")
        
    # Final save
    with open(progress_file, 'w', encoding='utf-8') as f:
        json.dump(progress, f, indent=2)

def scrape_article(url, storage, progress, global_settings):
    try:
        headers = {
            "User-Agent": global_settings.get('user_agent', 'Mozilla/5.0')
        }
        resp = requests.get(url, headers=headers, timeout=20)
        if resp.status_code != 200:
            return False
            
        soup = BeautifulSoup(resp.text, 'html.parser')
        
        # Updated selector: .titleItemNews
        title_el = soup.select_one('.titleItemNews')
        title = title_el.get_text(strip=True) if title_el else ""
        
        # Updated selector: .contentDetail
        content_el = soup.select_one('.contentDetail')
        if not content_el:
            content_el = soup.select_one('.ContentDetail') # fallback
            
        if not content_el:
            return False
            
        # Sapo
        sapo_el = soup.select_one('.sumary')
        sapo = sapo_el.get_text(strip=True) if sapo_el else ""
            
        # Author
        author_el = soup.select_one('.author')
        author = author_el.get_text(strip=True) if author_el else "Tạp chí Cộng Sản"

        # Convert to markdown
        content_md = ""
        if sapo:
            content_md += f"> {sapo}\n\n"
        content_md += md(str(content_el), heading_style="ATX")
        
        # Date
        date_el = soup.select_one('.time-post')
        date_str = date_el.get_text(strip=True) if date_el else ""
        
        success = storage.insert_article(
            url=url,
            title=title,
            author=author,
            content=content_md,
            metadata={"publish_date": date_str, "sapo": sapo}
        )
        
        if success:
            progress['scraped_urls'][url] = time.time()
            return True
            
    except Exception as e:
        print(f"Failed to scrape {url}: {e}")
    return False
