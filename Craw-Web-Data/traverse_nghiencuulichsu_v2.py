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

def get_locs(url):
    print(f"Fetching: {url}")
    r = requests.get(url, headers=headers)
    if r.status_code == 200:
        soup = BeautifulSoup(r.text, 'xml')
        locs = [loc.text for loc in soup.find_all('loc')]
        return locs
    else:
        print(f"Failed: {url} (Status: {r.status_code})")
        return []

root_locs = get_locs("https://nghiencuulichsu.com/sitemap.xml")
print(f"Found {len(root_locs)} locs in root.")
for i, loc in enumerate(root_locs[:20]):
    print(f"  [{i}] {loc}")

# Analyze if it's an index or direct posts
index_locs = [l for l in root_locs if "sitemap" in l and l.endswith(".xml")]
if index_locs:
    print(f"Detected {len(index_locs)} sub-sitemaps.")
else:
    print("No sub-sitemaps detected. Root seems to contain direct URLs.")
