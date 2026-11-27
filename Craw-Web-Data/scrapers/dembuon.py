from scrapers.xenforo_crawler import process_xenforo

BOARDS = [
    'truyen-cua-toi.206', 'review-dao.93', 'linh-tinh-khac.98', 'clb-hoc-tap.103',
    'hoi-phu-nu.88', 'tra-da-tan-gau.48', 'kinh-nghiem-song.54', 'thu-phap.136',
    'sach-hay.138', 'kien-thuc.44', 'binh-luan-van-hoc.15'
]

def process(source, global_settings):
    process_xenforo(source, global_settings, BOARDS, "https://dembuon.vn/", max_pages=50)
