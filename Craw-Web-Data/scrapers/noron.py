import os
import json
import time
from bs4 import BeautifulSoup
from curl_cffi import requests
from markdownify import markdownify as md
from scrapers.storage_utils import get_storage
from playwright.sync_api import sync_playwright, TimeoutError

def process(source, global_settings):
    storage = get_storage(source['id'])
    progress_file = source.get('progress_file', f"progress_{source.get('id')}.json")
    
    progress = {}
    if os.path.exists(progress_file):
        try:
            with open(progress_file, 'r', encoding='utf-8') as f:
                progress = json.load(f)
        except:
            pass
            
    if 'scraped_urls' not in progress:
        progress['scraped_urls'] = {}

    print(f"Starting Noron crawler (Playwright-based)")
    
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        # Using a standard user agent
        page = browser.new_page(user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36")
        
        try:
            # Let's crawl recent questions from the homepage
            print("  Fetching Homepage to find post links...")
            page.goto("https://noron.vn/", wait_until='networkidle', timeout=30000)
            
            # Extract links
            links = page.evaluate('''() => {
                let anchors = Array.from(document.querySelectorAll('a'));
                return anchors.map(a => a.href).filter(href => href.includes('/post/'));
            }''')
            links = list(set(links))
            print(f"  Found {len(links)} post links on homepage.")
            
            count = 0
            for url in links:
                if url in progress['scraped_urls']:
                    continue
                    
                print(f"  Processing {url}")
                try:
                    page.goto(url, wait_until='networkidle', timeout=30000)
                    # wait a bit for react to render
                    page.wait_for_timeout(2000)
                    
                    # Extract title
                    title = page.evaluate('() => { let el = document.querySelector(".post-title-header") || document.querySelector("h1") || document.querySelector(".title"); return el ? el.innerText : ""; }')
                    
                    if not title:
                        print(f"    No title found, skipping.")
                        continue
                        
                    # Find all comment elements
                    # Usually Noron has a container with 'comment' or 'reply' classes or elements with id 'comment-...' 
                    comments_html = page.evaluate('''() => {
                        // find all comment-like divs
                        let nodes = document.querySelectorAll('.comment-item, .box-comment, [class*="comment"], div[id^="comment-"]');
                        // Filter out empty or too small
                        let valid = Array.from(nodes).filter(n => n.innerText && n.innerText.length > 10 && n.className !== 'comment-list');
                        
                        // We will just gather the inner texts but we want markdown. So we get innerHTML
                        return valid.map(n => n.outerHTML).join('\\n--- \\n');
                    }''')
                    
                    if not comments_html or len(comments_html) < 20:
                        print(f"    No comments found for {title[:30]}")
                        # Even if no comments, we might want to save the title. But user asked for Q&A data.
                        comments_html = "Không có bình luận nào."
                    
                    # Convert comments to Markdown
                    # The constraint is to EXCLUDE the main article totally.
                    content_md = md(comments_html, heading_style="ATX").strip()
                    full_content = f"**CÂU HỎI TÓM TẮT:** {title}\n\n**BÌNH LUẬN / TRẢ LỜI:**\n\n{content_md}"
                    
                    success = storage.insert_article(
                        url=url,
                        title=title.strip(),
                        content=full_content,
                        metadata={"type": "q_and_a", "source": "noron.vn"}
                    )
                    
                    if success:
                        count += 1
                        progress['scraped_urls'][url] = time.time()
                        with open(progress_file, 'w', encoding='utf-8') as f:
                            json.dump(progress, f, indent=2)
                            
                except TimeoutError:
                    print(f"    Timeout loading {url}")
                except Exception as e:
                    print(f"    Error scraping {url}: {e}")
                    
            print(f"  Scraped {count} new posts from Noron.")
            
        except Exception as e:
            print(f"Error in Noron process: {e}")
            
        finally:
            browser.close()
