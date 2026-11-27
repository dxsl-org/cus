import requests
import json
import time

url_post = "https://nghiencuulichsu.com/wp-json/wp/v2/posts?per_page=1"
url_sitemap = "https://nghiencuulichsu.com/sitemap.xml"

headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
    "Accept-Language": "en-US,en;q=0.9,vi;q=0.8",
    "Cache-Control": "max-age=0",
    "Upgrade-Insecure-Requests": "1"
}

def check(url, name):
    print(f"Checking {name}: {url}")
    try:
        r = requests.get(url, headers=headers, timeout=15)
        print(f"Status: {r.status_code}")
        print(f"Headers: {r.headers}")
        print(f"Content (first 200 chars): {r.text[:200]}")
        if r.status_code == 200:
            return True
    except Exception as e:
        print(f"Error: {e}")
    return False

check(url_post, "WP-JSON API")
check(url_sitemap, "Sitemap")
