import unicodedata

from pipeline.process import filters_vi
from pipeline.process.normalize import normalize

ARTICLE = (
    "Sáng nay, Ủy ban nhân dân thành phố đã tổ chức hội nghị tổng kết công tác phòng chống thiên tai năm qua. "
    "Theo báo cáo, các địa phương đã chủ động di dời hàng nghìn hộ dân ra khỏi vùng nguy hiểm trước khi bão đổ bộ. "
    "Nhiều tuyến đê xung yếu được gia cố kịp thời, nhờ vậy thiệt hại về người và tài sản giảm đáng kể so với năm trước.\n\n"
    "Phát biểu tại hội nghị, lãnh đạo thành phố đề nghị các sở ngành tiếp tục rà soát kế hoạch ứng phó, "
    "đặc biệt là ở những khu vực ven biển thường xuyên bị ngập. Người dân cũng được khuyến cáo theo dõi "
    "thông tin dự báo thời tiết và chuẩn bị sẵn lương thực, nước uống khi có cảnh báo."
)


def test_normalize_repairs_unicode_but_keeps_vietnamese_quotes_and_nfc():
    decomposed = unicodedata.normalize("NFD", "Hội thảo “Cập nhật” ![ảnh](http://x/y.jpg) [nguồn](http://x)")
    out = normalize(decomposed)
    assert unicodedata.is_normalized("NFC", out)
    assert out == "Hội thảo “Cập nhật” nguồn"
    assert normalize("Are You suprised ?\nNội dung văn bản", "vbpl_vn") == "Nội dung văn bản"


def test_normal_article_passes_and_short_or_garbled_text_fails():
    assert filters_vi.check(ARTICLE)[0] is None
    assert filters_vi.check("Tin ngắn.")[0] == "too_short"
    assert filters_vi.check("N u ễ Huỳ T ị Hươ Th " * 40)[0] == "garbled"
    assert filters_vi.check("KiÓm s¸t viªn më phiªn toµ xÐt xö " * 20)[0] == "garbled"


def test_repeated_boilerplate_is_caught():
    spam = "\n".join(["Mua ngay hôm nay để nhận ưu đãi lớn cho khách hàng thân thiết của cửa hàng."] * 30)
    assert filters_vi.check(spam)[0] in {"dup_lines", "dup_paragraphs"} | {f"top_{n}gram" for n in (3, 4, 6)}
