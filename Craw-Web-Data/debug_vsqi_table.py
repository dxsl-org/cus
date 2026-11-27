import requests
from bs4 import BeautifulSoup

url = 'https://tieuchuan.vsqi.gov.vn/tieuchuan/view?sohieu=TCVN+10544-2%3A2026'
resp = requests.get(url, headers={"User-Agent": "Mozilla/5.0"})
soup = BeautifulSoup(resp.text, 'html.parser')
tables = soup.find_all('table')
print(f"Found {len(tables)} tables")
for t in tables:
    print("Table classes:", t.get('class'))
    print("Parent classes:", t.parent.get('class'))
    for tr in t.find_all('tr')[:2]:
        print([td.get_text(strip=True) for td in tr.find_all(['td', 'th'])])
