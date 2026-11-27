import requests
import json

with open("nghiencuulichsu_cookies.json", "r") as f:
    cookies_data = json.load(f)

cookie_str = "; ".join([f"{c['name']}={c['value']}" for c in cookies_data])
headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Cookie": cookie_str
}

url = "https://nghiencuulichsu.com/2026/03/05/nam-viet-viet-nam/"
r = requests.get(url, headers=headers)
with open("C:/tmp/nghiencuulichsu_sample.html", "w", encoding="utf-8") as f:
    f.write(r.text)
print("Dumped HTML to C:/tmp/nghiencuulichsu_sample.html")
