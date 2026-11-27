"""
AloBacsi Scraper Plugin - Sitemap based
"""
import os
import re
import json
import time
import requests
from bs4 import BeautifulSoup
from markdownify import markdownify as md
import xml.etree.ElementTree as ET

from scrapers.storage_utils import get_storage

def process(source, global_settings):
    base_url = source.get('url', "https://alobacsi.com").rstrip('/')
    sitemap_index_url = f"{base_url}/sitemap.xml"
    progress_file = source.get('progress_file', f"progress_{source['id']}.json")
    
    # Initialize SQLite Storage
    storage = get_storage(source['id'])
    
    progress = {}
    if os.path.exists(progress_file):
        with open(progress_file, 'r', encoding='utf-8') as f:
            try:
                progress = json.load(f)
            except:
                progress = {}
            
    headers = {
        "User-Agent": global_settings.get('user_agent', "Mozilla/5.0")
    }
    
    print(f"Starting AloBacsi comprehensive crawler (Storage: SQLite)...")
    
    session = requests.Session()
    session.headers.update(headers)
    
    try:
        print(f"Fetching sitemap index: {sitemap_index_url}")
        resp = session.get(sitemap_index_url, timeout=30)
        if resp.status_code != 200:
            print(f"Failed to fetch sitemap index: {resp.status_code}")
            return
            
        # Parse sitemap index
        # Use regex to find <loc> since namespaces can be tricky with ET
        sub_sitemaps = re.findall(r'<loc>(https://alobacsi\.com/sitemap-.*?\.xml)</loc>', resp.text)
        
        # Filter for relevant sitemaps
        relevant_patterns = [
            'kham-benh-online',
            'tin-tuc',
            'cam-nang-suc-khoe',
            'benh',
            'tu-van-suc-khoe',
            'alobacsi-cong-dong',
            'dich-vu-y',
            'bac-si',
            'thuoc',
            'co-so-y-te'
        ]
        
        filtered_sitemaps = []
        for s in sub_sitemaps:
            if any(p in s for p in relevant_patterns):
                filtered_sitemaps.append(s)
        
        print(f"Found {len(filtered_sitemaps)} relevant sub-sitemaps.")
        
        for s_url in filtered_sitemaps:
            print(f"Processing sub-sitemap: {s_url}")
            try:
                s_resp = session.get(s_url, timeout=30)
                if s_resp.status_code == 200:
                    urls = re.findall(r'<loc>(.*?)</loc>', s_resp.text)
                    print(f"  Found {len(urls)} URLs in {s_url}")
                    
                    for url in urls:
                        if url in progress.get('scraped_urls', {}):
                            continue
                            
                        scrape_page(url, storage, progress, session, global_settings)
                        
                        # Save progress frequently
                        with open(progress_file, 'w', encoding='utf-8') as f:
                            json.dump(progress, f, indent=2)
                else:
                    print(f"  Failed to fetch sub-sitemap {s_url}: {s_resp.status_code}")
            except Exception as e:
                print(f"  Error processing sub-sitemap {s_url}: {e}")
                
    except Exception as e:
        print(f"Critical error in AloBacsi process: {e}")

def scrape_page(url, storage, progress, session, global_settings):
    try:
        # Avoid index pages and common non-content pages
        # Article/QA URLs on AloBacsi are usually deep and end with .html
        if url.endswith('/') or not url.endswith('.html'):
             return
             
        # Skip purely category list pages that might end in .html (if any)
        # But most are already excluded by the basic check above
        
        print(f"Scraping: {url}")
        resp = session.get(url, timeout=30)
        if resp.status_code != 200:
            return
            
        soup = BeautifulSoup(resp.text, 'html.parser')
        
        # Title selection
        title_elem = soup.select_one('h1.detail-title, .title-detail, .story__heading, h1')
        if not title_elem:
            print(f"  Debug: No title found for {url}")
            return
        title = title_elem.get_text(strip=True)
        
        # Content selection
        # For Q&A pages
        question_elem = soup.select_one('.detail-content, .content-detail')
        answer_elem = soup.select_one('.doctor-answer, .answer-content')
        
        # For News/Articles
        article_elem = soup.select_one('.detail-content, .content-detail, .story__summary, .story__content, .detail_block')
        
        if not question_elem and not article_elem:
            # Try a broader selector for general articles
            article_elem = soup.select_one('article .content, .post-content, div[itemprop="articleBody"]')
            
        if not question_elem and not article_elem:
            print(f"  Debug: No content found for {url}")
            return
            
        full_content = ""
        if answer_elem:
            # It's a Q&A
            question_md = md(str(question_elem)) if question_elem else ""
            answer_md = md(str(answer_elem))
            full_content = f"## Câu hỏi\n\n{question_md}\n\n---\n\n## Trả lời của Bác sĩ\n\n{answer_md}"
            meta_type = "QA"
        else:
            # It's a regular article
            content_md = md(str(article_elem))
            full_content = content_md
            meta_type = "Article"
            
        print(f"  Debug: Extracted {len(full_content)} chars from {url} (Type: {meta_type})")
        
        # Insert into SQLite
        success = storage.insert_article(
            url=url,
            title=title,
            content=full_content,
            metadata={"type": meta_type}
        )
        
        if success:
            print(f"  Debug: Successfully inserted {url}")
            if 'scraped_urls' not in progress: progress['scraped_urls'] = {}
            progress['scraped_urls'][url] = time.time()
        else:
            print(f"  Debug: Failed to insert {url}")
        
        # Rate limiting
        time.sleep(global_settings.get('request_delay', 0.5))
        
    except Exception as e:
        print(f"Error scraping {url}: {e}")
