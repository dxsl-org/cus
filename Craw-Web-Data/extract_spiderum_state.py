from bs4 import BeautifulSoup
import json
import re

def extract_state():
    with open('C:/tmp/spiderum_test.html', 'r', encoding='utf-8') as f:
        html = f.read()
    
    soup = BeautifulSoup(html, 'html.parser')
    
    for i, script in enumerate(soup.find_all('script')):
        content = script.string or ''
        if 'post' in content and 'title' in content and len(content) > 5000:
            print(f"Candidate Script found (index {i}, len {len(content)})")
            # Try to find the JSON part
            # Often it's window.__INITIAL_STATE__ = { ... }
            # Or it might be just a large string in a specific format
            
            # Let's save it for inspection
            with open(f'C:/tmp/spiderum_script_{i}.txt', 'w', encoding='utf-8') as f2:
                f2.write(content)
            
            # Try to decode if it looks like &q;
            if '&q;' in content:
                decoded = content.replace('&q;', '"').replace('&a;', '&').replace('&l;', '<').replace('&g;', '>')
                try:
                    # Look for first { and last }
                    start = decoded.find('{')
                    end = decoded.rfind('}')
                    if start != -1 and end != -1:
                        json_str = decoded[start:end+1]
                        data = json.loads(json_str)
                        print("SUCCESS: Decoded JSON state!")
                        
                        # Find the post key
                        post_key = next((k for k in data.keys() if 'api/v1/post/' in k), None)
                        if post_key:
                            print(f"Found post key: {post_key}")
                            post_data = data[post_key] # Might be the post directly or wrapped
                            if 'post' in post_data:
                                post_data = post_data['post']
                            
                            print(f"Post Title: {post_data.get('title')}")
                            print(f"Content Length (HTML): {len(post_data.get('content', ''))}")
                            print(f"Snippet: {post_data.get('content', '')[:200]}...")
                        else:
                            print("Post key not found in state.")
                except Exception as e:
                    print(f"Failed to parse decoded JSON: {e}")

if __name__ == "__main__":
    extract_state()
