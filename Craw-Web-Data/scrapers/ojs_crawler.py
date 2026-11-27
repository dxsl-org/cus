"""
Unified OJS (Open Journal Systems) Scraper Plugin
Targets OJS-based academic journals like vjmed.vn, vjpm.vn
"""
import os
import re
import json
import time
import requests
from bs4 import BeautifulSoup
from markdownify import markdownify as md
from urllib.parse import urljoin

from scrapers.storage_utils import get_storage

def process(source, global_settings):
    base_url = source.get('url').rstrip('/')
    progress_file = source.get('progress_file', f"progress_{source.get('id')}.json")
    
    # Initialize SQLite Storage
    storage = get_storage(source['id'])
    
    # Load progress
    progress = {}
    if os.path.exists(progress_file):
        with open(progress_file, 'r', encoding='utf-8') as f:
            progress = json.load(f)
            
    headers = {
        "User-Agent": global_settings.get('user_agent', "Mozilla/5.0")
    }
    
    print(f"Starting OJS crawler for {source['name']} (Storage: SQLite) at {base_url}")
    
    # Discovery: Try different OJS path variants
    articles_to_scrape = []
    discovery_paths = [
        "issue/archive",
        "index.php/vjmed/issue/archive",
        "index.php/vjpm/issue/archive",
        "index.php/vmj/issue/archive",
        "archives"
    ]
    
    for path in discovery_paths:
        archives_url = urljoin(base_url, path)
        print(f"Trying discovery at: {archives_url}")
        try:
            resp = requests.get(archives_url, headers=headers, timeout=30, allow_redirects=True)
            if resp.status_code == 200 and ("issue" in resp.url or "archive" in resp.url):
                soup = BeautifulSoup(resp.text, 'html.parser')
                # Find issue links
                issue_links = [urljoin(resp.url, a.get('href')) for a in soup.select('a.obj_issue_summary_title, .issues_list a, .issue-summary a')]
                if not issue_links:
                    continue
                    
                for issue_url in issue_links:
                    if issue_url in progress.get('scanned_issues', []):
                        continue
                    
                    print(f"Scanning issue: {issue_url}")
                    i_resp = requests.get(issue_url, headers=headers, timeout=30)
                    if i_resp.status_code == 200:
                        i_soup = BeautifulSoup(i_resp.text, 'html.parser')
                        # Find article links
                        a_links = [urljoin(issue_url, a.get('href')) for a in i_soup.select('div.obj_article_summary h3 a, .article_summary a, .article-summary a')]
                        for a_link in a_links:
                            if a_link not in progress.get('scraped_articles', {}):
                                articles_to_scrape.append(a_link)
                                
                    if 'scanned_issues' not in progress: progress['scanned_issues'] = []
                    progress['scanned_issues'].append(issue_url)
                
                # If we found links, we can stop trying other paths
                if articles_to_scrape:
                    break
        except Exception as e:
            print(f"Error at {archives_url}: {e}")

    # Process articles
    for url in articles_to_scrape:
        print(f"Scraping article: {url}")
        try:
            resp = requests.get(url, headers=headers, timeout=30)
            if resp.status_code == 200:
                soup = BeautifulSoup(resp.text, 'html.parser')
                
                title = soup.select_one('h1.page_title, .article_details h1').get_text(strip=True) if soup.select_one('h1.page_title, .article_details h1') else "No Title"
                authors = [a.get_text(strip=True) for a in soup.select('.authors .name, .author_name')]
                abstract_elem = soup.select_one('.abstract, .article_abstract')
                abstract = md(str(abstract_elem)) if abstract_elem else ""
                
                # Insert into SQLite
                success = storage.insert_article(
                    url=url,
                    title=title,
                    author=", ".join(authors),
                    content=abstract,
                    metadata={"source": "OJS"}
                )
                
                if success:
                    if 'scraped_articles' not in progress: progress['scraped_articles'] = {}
                    progress['scraped_articles'][url] = time.time()
                
                # Save progress periodically
                with open(progress_file, 'w', encoding='utf-8') as f:
                    json.dump(progress, f, indent=2)
                    
            time.sleep(global_settings.get('request_delay', 1))
        except Exception as e:
            print(f"Failed to scrape {url}: {e}")

    print(f"Finished OJS crawler for {source['name']}")
