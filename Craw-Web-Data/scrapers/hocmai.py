from scrapers.xenforo_crawler import process_xenforo

BOARDS = [
    'hoc-thong-minh.3053', 'bang-tin-phong-sinh-hoat-chung.2283', 'ban-tin-tu-van-tuyen-sinh.211',
    'cac-mon-khac.1829', 'hmf-radio-cho-ban-cho-toi.2710', 'danh-cho-ba-me.51',
    'toan.116', 'ngu-van.115', 'tieng-anh.114', 'vat-li.117', 'hoa-hoc.118', 'sinh-hoc.119',
    'lich-su.120', 'dia-li.121', 'giao-duc-cong-dan.122',
    'lich-su.148', 'dia-li.149'
]

def process(source, global_settings):
    process_xenforo(source, global_settings, BOARDS, "https://diendan.hocmai.vn/", max_pages=50)
