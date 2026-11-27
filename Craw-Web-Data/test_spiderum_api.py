import requests
import json

def test():
    with open("C:/tmp/spiderum_credentials.json", "r", encoding='utf-8') as f:
        creds = json.load(f)
    
    slug = "Cha-toi-khong-xung-dang-duoc-yeu-thuong-RNNJNu"
    api_url = f"https://spiderum.com/api/v1/posts/getPostDetail?postSlug={slug}"
    
    headers = {
        "User-Agent": creds["user_agent"],
        "Referer": f"https://spiderum.com/bai-dang/{slug}",
        "Accept": "application/json, text/plain, */*",
        "X-Requested-With": "XMLHttpRequest"
    }
    
    cookies = creds["cookies"]
    
    resp = requests.get(api_url, headers=headers, cookies=cookies)
    print(f"Status: {resp.status_code}")
    if resp.status_code == 200:
        try:
            print(f"JSON Keys: {resp.json().keys()}")
        except:
            print(f"Response not JSON. Content snippet: {resp.text[:200]}")
    else:
        print(f"Error: {resp.text[:200]}")

if __name__ == "__main__":
    test()
