import requests
from bs4 import BeautifulSoup

url = 'https://tieuchuan.vsqi.gov.vn/tieuchuan/view?sohieu=TCVN+10544-2%3A2026'
HEADERS = {"User-Agent": "Mozilla/5.0"}

resp = requests.get(url, headers=HEADERS)
soup = BeautifulSoup(resp.text, 'html.parser')

print("Status:", resp.status_code)
metadata = {}
info_table = soup.select_one('.table-responsive table')
if info_table:
    for tr in info_table.find_all('tr'):
        tds = tr.find_all('td')
        if len(tds) >= 2:
            key = tds[0].get_text(strip=True).replace(':', '')
            value = tds[1].get_text(strip=True)
            if key and value:
                metadata[key] = value

print("Extracted metadata:", metadata)

so_hieu = metadata.get('Số hiệu tiêu chuẩn') or metadata.get('Standard Number')
print("so_hieu:", so_hieu)
