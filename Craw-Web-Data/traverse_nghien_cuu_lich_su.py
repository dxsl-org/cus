import requests
import json
from bs4 import BeautifulSoup

with open("nghiencuulichsu_cookies.json", "r") as f:
    cookies_data = json.load(f)

cookie_str = "; ".join([f"{c['name']}={c['value']}" for c in cookies_data])
headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Cookie": cookie_str
}

def get_sitemaps(url):
    print(f"Fetching sitemap: {url}")
    r = requests.get(url, headers=headers)
    if r.status_code == 200:
        soup = BeautifulSoup(r.text, 'xml')
        locs = [loc.text for loc in soup.find_all('loc')]
        return locs
    else:
        print(f"Failed to fetch sitemap: {url} (Status: {r.status_code})")
        return []

root_sitemaps = get_sitemaps("https://nghiencuulichsu.com/sitemap.xml")
print(f"Found {len(root_sitemaps)} root sitemaps.")
for sm in root_sitemaps:
    if "post-sitemap" in sm:
        posts = get_sitemaps(sm)
        print(f"  {sm}: Found {len(posts)} posts. Sample: {posts[0] if posts else 'None'}")
