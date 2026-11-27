import cloudscraper
import json

scraper = cloudscraper.create_scraper()
url_post = "https://nghiencuulichsu.com/wp-json/wp/v2/posts?per_page=1"
url_sitemap = "https://nghiencuulichsu.com/sitemap.xml"

def check(url, name):
    print(f"Checking {name} with cloudscraper: {url}")
    try:
        r = scraper.get(url, timeout=20)
        print(f"Status: {r.status_code}")
        print(f"Content (first 200 chars): {r.text[:200]}")
        if r.status_code == 200:
            return True
    except Exception as e:
        print(f"Error: {e}")
    return False

check(url_post, "WP-JSON API")
check(url_sitemap, "Sitemap")
