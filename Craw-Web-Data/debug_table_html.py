import requests
from bs4 import BeautifulSoup
url = 'https://tieuchuan.vsqi.gov.vn/tieuchuan/view?sohieu=TCVN+8764%3A2012'
resp = requests.get(url, headers={"User-Agent": "Mozilla/5.0"})
soup = BeautifulSoup(resp.text, 'html.parser')
t = soup.find('table', class_='detail-view')
if t:
    with open('D:/Data/debug_table.html', 'w', encoding='utf-8') as f:
        f.write(t.prettify())
    print("Saved to debug_table.html")
else:
    print("Table not found")
