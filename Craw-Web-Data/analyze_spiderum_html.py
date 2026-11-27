from bs4 import BeautifulSoup
import json

def analyze():
    with open('C:/tmp/spiderum_test.html', 'r', encoding='utf-8') as f:
        html = f.read()
    
    soup = BeautifulSoup(html, 'html.parser')
    
    # 1. Title
    h1 = soup.find('h1')
    print(f"H1: {h1.get_text().strip() if h1 else 'NONE'}")
    
    # 2. Author
    author = soup.select_one('a[class*="author"], div[class*="author"], a[class*="creator"]')
    print(f"Author Candidate: {author.get_text().strip() if author else 'NONE'}")
    
    # 3. Date
    date = soup.select_one('time, span[class*="date"], div[class*="date"]')
    print(f"Date Candidate: {date.get_text().strip() if date else 'NONE'}")
    
    # 4. Content
    # Let's try to find the biggest text block
    divs = soup.find_all('div')
    sorted_divs = sorted(divs, key=lambda d: len(d.get_text()), reverse=True)
    for i, d in enumerate(sorted_divs[:10]):
        cls = d.get('class', [])
        print(f"Div {i} (len {len(d.get_text())}): Class={cls} Snippet={d.get_text()[:50]}...")

if __name__ == "__main__":
    analyze()
