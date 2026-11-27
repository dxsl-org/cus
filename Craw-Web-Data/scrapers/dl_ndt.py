import requests

url = 'https://nguoidothi.net.vn/quoc-hoi-neu-nhung-yeu-to-gay-lang-phi-can-tap-trung-khac-phuc-tai-tp-hcm-35897.html'
html = requests.get(url, headers={'User-Agent': "Mozilla/5.0"}).text
with open('d:/Data/ndt.html', 'w', encoding='utf-8') as f:
    f.write(html)
