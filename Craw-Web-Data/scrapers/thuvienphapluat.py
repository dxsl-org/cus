from playwright.sync_api import sync_playwright
from bs4 import BeautifulSoup
import json
import os
import time
import random
from datetime import datetime
from markdownify import markdownify as md
from scrapers.storage_utils import get_storage

USER_AGENTS = [
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36',
    'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36'
]

def load_page_with_retry(page, url, max_retries=3):
    for i in range(max_retries):
        try:
            page.goto(url, wait_until='domcontentloaded', timeout=30000)
            
            # Check for Cloudflare challenge and wait if necessary
            heading = page.locator('h1').count()
            if heading == 0 and 'Just a moment...' in page.content():
                 print(f"  Cloudflare challenge detected, waiting...")
                 page.wait_for_timeout(10000) # Give Turnstile time to run
            
            return page.content()
        except Exception as e:
            print(f"  Playwright timeout/error on {url}: {e}")
            time.sleep(5)
    return None

def process(source, global_settings):
    source_id = source['id']
    storage = get_storage(source_id)
    
    # We will scrape the mobile listing as it bypasses Cloudflare faster, 
    # but actual articles need to be scraped from the desktop site.
    sections = [
        {"url": "https://m.thuvienphapluat.vn/hoi-dap-phap-luat", "category": "Hỏi đáp pháp luật"},
        {"url": "https://m.thuvienphapluat.vn/chinh-sach-phap-luat-moi", "category": "Chính sách pháp luật mới"}
    ]
    
    progress_file = f"progress_{source_id}.json"
    progress = {}
    if os.path.exists(progress_file):
        with open(progress_file, 'r', encoding='utf-8') as f:
            progress = json.load(f)
            
    scraped_urls = set(progress.get('scraped_urls', []))
    
    print("Starting Playwright for Thư Viện Pháp Luật...")
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=[
                '--disable-blink-features=AutomationControlled',
                '--no-sandbox',
                '--disable-setuid-sandbox'
            ]
        )
        context = browser.new_context(
            user_agent=random.choice(USER_AGENTS),
            viewport={'width': 1920, 'height': 1080}
        )
        page = context.new_page()
        
        # Initial visit to acquire cookies
        print("Acquiring session cookies...")
        load_page_with_retry(page, "https://thuvienphapluat.vn/")
        time.sleep(2)
        
        for section in sections:
            print(f"Processing section: {section['category']}")
            current_page = progress.get(f"last_page_{section['category']}", 1)
            
            while True:
                page_url = f"{section['url']}?page={current_page}"
                print(f"  Fetching page {current_page}: {page_url}")
                
                html_content = load_page_with_retry(page, page_url)
                if not html_content:
                    print("  Failed to load listing page.")
                    break
                    
                soup = BeautifulSoup(html_content, 'html.parser')
                links = soup.select('a.title-document')
                
                if not links:
                    print("  No more links found in this section.")
                    break
                    
                new_articles = []
                for link in links:
                    url = link.get('href')
                    if not url: continue
                    
                    if url.startswith('/'):
                        url = "https://thuvienphapluat.vn" + url
                    elif url.startswith('http://m.') or url.startswith('https://m.'):
                        url = url.replace('m.thuvienphapluat.vn', 'thuvienphapluat.vn')
                        
                    if url in scraped_urls:
                        continue
                        
                    article_data = scrape_article(page, url, section['category'])
                    if article_data:
                        new_articles.append(article_data)
                        scraped_urls.add(url)
                        time.sleep(random.uniform(2, 5))
                
                if new_articles:
                    storage.insert_articles(new_articles)
                    print(f"  Saved {len(new_articles)} articles from page {current_page}")
                else:
                    print(f"  No new articles on page {current_page}")
                    if current_page > 5:
                         break 

                current_page += 1
                progress[f"last_page_{section['category']}"] = current_page
                progress['scraped_urls'] = list(scraped_urls)
                with open(progress_file, 'w', encoding='utf-8') as f:
                    json.dump(progress, f, ensure_ascii=False, indent=4)
                
                time.sleep(random.uniform(5, 10))
                
        browser.close()

def scrape_article(page, url, category):
    print(f"    Scraping article: {url}")
    html_content = load_page_with_retry(page, url)
    if not html_content:
        return None
        
    try:
        soup = BeautifulSoup(html_content, 'html.parser')
        title_tag = soup.find('h1')
        title = title_tag.get_text(strip=True) if title_tag else ""
        if not title:
            print(f"    Warning: No title found for {url}, might be Cloudflare blocked.")
            return None
            
        content_div = soup.select_one('.content-news-detail') or soup.select_one('.content')
        if not content_div:
            return None
            
        for junk in content_div.select('.related-news, .ads-container, script, style, .div-facebook-box, .banner-ads, .box-social'):
            junk.decompose()
            
        content_md = md(str(content_div))
        
        author = ""
        author_meta = soup.find('meta', {'name': 'author'})
        if author_meta: author = author_meta.get('content', '')
            
        date_str = ""
        date_meta = soup.find('meta', {'property': 'article:published_time'}) or soup.find('meta', {'itemprop': 'datePublished'})
        if date_meta: date_str = date_meta.get('content', '')
            
        tags = [t.get_text(strip=True) for t in soup.select('.tags a')]
        
        return {
            "url": url, "title": title, "author": author,
            "content": content_md, "category": category,
            "tags": tags, "metadata": {"published_at": date_str}
        }
    except Exception as e:
        print(f"    Error parsing {url}: {e}")
        return None
