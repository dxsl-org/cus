from scrapers.xenforo_crawler import process_xenforo

BOARDS = [
    'suy-ngam.238', 'meo-vat.249', 'ky-nang-song.217', 'viet-van-phong.198',
    'thu-vien-tai-lieu.193', 'luyen-tieng-anh.425', 'ngoai-ngu-khac.455',
    'hoi-dap.328', 'chia-se.218', 'giao-luu-ket-ban.16',
    'sharing-stories.5', 'conan-fan-fiction.12', 'cuoi-chut-cho-vui.19'
]

def process(source, global_settings):
    process_xenforo(source, global_settings, BOARDS, "https://kenhsinhvien.vn/", forum_path="/f/", thread_path="/t/", max_pages=100)
