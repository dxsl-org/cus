import requests
import json
from bs4 import BeautifulSoup
from markdownify import markdownify as md

with open("nghiencuulichsu_cookies.json", "r") as f:
    cookies_data = json.load(f)

cookie_str = "; ".join([f"{c['name']}={c['value']}" for c in cookies_data])
headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Cookie": cookie_str
}

url = "https://nghiencuulichsu.com/2026/03/05/nam-viet-viet-nam/"
print(f"Verifying selectors for: {url}")

r = requests.get(url, headers=headers, timeout=15)
if r.status_code == 200:
    soup = BeautifulSoup(r.text, 'html.parser')
    
    title = soup.select_one('h1.entry-title').get_text(strip=True) if soup.select_one('h1.entry-title') else "No Title"
    content_el = soup.select_one('.entry-content')
    content_md = md(str(content_el), heading_style="ATX") if content_el else "No Content"
    
    author = "Unknown"
    author_el = soup.select_one(".entry-meta .author a")
    if author_el:
        author = author_el.get_text(strip=True)
        
    date = ""
    date_el = soup.select_one("time.entry-date")
    if date_el:
        date = date_el.get("datetime") or date_el.get_text(strip=True)
        
    category = ""
    cat_el = soup.select_one(".cat-links a")
    if cat_el:
        category = cat_el.get_text(strip=True)

    print(f"Title: {title}")
    print(f"Author: {author}")
    print(f"Date: {date}")
    print(f"Category: {category}")
    print(f"Content length: {len(content_md)}")
    print(f"Content snippet: {content_md[:200]}...")
else:
    print(f"Failed to fetch article: {r.status_code}")
