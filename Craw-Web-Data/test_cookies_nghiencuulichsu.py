import requests
import json

with open("nghiencuulichsu_cookies.json", "r") as f:
    cookies_data = json.load(f)

cookie_str = "; ".join([f"{c['name']}={c['value']}" for c in cookies_data])
headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Cookie": cookie_str,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8"
}

print("Testing Sitemap with cookies...")
url_sitemap = "https://nghiencuulichsu.com/sitemap.xml"
r = requests.get(url_sitemap, headers=headers, timeout=15)
print(f"Sitemap Status: {r.status_code}")
if r.status_code == 200:
    print(f"Content (first 500 chars): {r.text[:500]}")
else:
    print(f"Sitemap Failed. Snippet: {r.text[:200]}")

print("\nTesting Home Page with cookies...")
r_home = requests.get("https://nghiencuulichsu.com", headers=headers, timeout=15)
print(f"Home Status: {r_home.status_code}")
if "Checking your browser" in r_home.text:
    print("STILL BLOCKED BY CLOUDFLARE IN REQUESTS.")
else:
    print("SUCCESS: Home page accessed with cookies.")
