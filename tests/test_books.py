import zipfile

from pipeline.books.classify_genre import parse
from pipeline.books.ebook_readers import read_paragraphs
from pipeline.books.encoding import classify
from pipeline.books.extract_ebook import _text
from pipeline.books.genre_report import tier
from pipeline.books.legacy_vn_fonts import tcvn3_to_unicode, vni_to_unicode
from pipeline.books.postprocess import clean

UNICODE = "Cộng hòa xã hội chủ nghĩa Việt Nam. Độc lập tự do hạnh phúc. Lịch sử Đồng Tháp Mười qua các thời kỳ."
TCVN3 = "Céng hßa x· héi chñ nghÜa ViÖt Nam. §éc lËp tù do h¹nh phóc. LÞch sö §ång Th¸p M\u00adêi qua c¸c thêi kú."
VNI = "Coäng hoøa xaõ hoäi chuû nghóa Vieät Nam. Ñoäc laäp töï do haïnh phuùc. Lòch söû Ñoàng Thaùp Möôøi qua caùc thôøi kyø."


def test_classify_tells_unicode_from_legacy_fonts_and_other_languages():
    assert classify(UNICODE) == "unicode"
    assert classify(TCVN3) == "tcvn3"
    assert classify(VNI) == "vni"
    assert classify("Histoire de la conquête française en Indochine et réformes économiques coloniales.") == "latin"


def test_legacy_fonts_convert_to_unicode():
    assert tcvn3_to_unicode(TCVN3) == UNICODE
    assert vni_to_unicode(VNI) == UNICODE
    # TCVN3 PDFs that render the ư slot as a minus sign; a real minus between digits stays.
    assert tcvn3_to_unicode("®−êng n−íc, 5 − 3") == "đường nước, 5 − 3"
    assert vni_to_unicode("VIEÄT NAM, ÑAÏI HOÏC, ÔÛ, ngöôøi, naêm, ñaëc") == "VIỆT NAM, ĐẠI HỌC, Ở, người, năm, đặc"


def test_clean_drops_running_headers_and_page_numbers_and_rejoins_wrapped_lines():
    # Body lines differ per page (as in a real book); only the header and the page number repeat.
    names = ["An", "Bình", "Cường", "Dũng", "Em"]
    body = "Đoàn tù binh thứ {} bị đày ra Côn Đảo năm ấy, cùng nhiều đảng viên Việt"
    pages = [f"LỊCH SỬ NHÀ TÙ CÔN ĐẢO\n{body.format(n)}\nMinh và các đồng chí khác ở Khám {i}.\n"
             f"Lính Âu-\nPhi đi đều bước kém tù binh {n}, nhận xét của người viết về sự kiện này rõ, ở trang infor-\n"
             f"mation {n}.\n{i + 10}" for i, n in enumerate(names)]
    pages[2] = "Chương 2\n" + pages[2]
    text = clean(pages)
    assert "LỊCH SỬ NHÀ TÙ" not in text and "\n10" not in text
    assert f"{body.format('An')} Minh và các đồng chí" in text  # full-width line + capitalised name joins
    assert "Lính Âu-Phi đi đều" in text  # a real Vietnamese hyphen stays
    assert "trang information An." in text  # a word hyphenated at the line end is rejoined
    assert "\nChương 2\n" in text  # a short heading stays on its own line


def test_genre_reply_rejects_codes_from_the_wrong_list_and_drives_ocr_tier():
    # Seen in the trial: a form code given as the field. It must become None, not a made-up field.
    reply = "Tóm tắt: số báo năm 1929\nLĩnh vực: su_lieu_dich\nThể loại: bao_tap_chi\nNgôn ngữ: VI"
    assert parse(reply) == (None, "bao_tap_chi", "vi")
    assert parse("Lĩnh vực: van_hoc\nThể loại: tieu_thuyet\nNgôn ngữ: vi") == ("van_hoc", "tieu_thuyet", "vi")
    assert tier("van_hoc", "tieu_thuyet", "vi") == 1
    assert tier("lich_su", "su_lieu_dich", "vi") == 2
    assert tier(None, "chuyen_khao", "vi") == 2  # unreadable reply: review before OCR, never dropped
    assert tier("lich_su", "chuyen_khao", "fr") == 3
    assert tier(None, "bao_tap_chi", "vi") == 3


def test_tcvn3_capital_font_heading_converts_to_upper_case_but_body_text_does_not():
    # .VnTimeH heading "PHẦN THỨ NHẤT" arrives as "PHÇN THø NHÊT"; Ç ø Ê are TCVN3's lower-case slots.
    assert tcvn3_to_unicode("PHÇN THø NHÊT\n" + TCVN3) == "PHẦN THỨ NHẤT\n" + UNICODE


def _docx(path, body):
    w = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("word/document.xml", f"<w:document {w}><w:body>{body}</w:body></w:document>")


def test_docx_paragraphs_keep_runs_together_and_verse_lines_apart(tmp_path):
    _docx(tmp_path / "a.docx", "<w:p><w:r><w:t>Chương </w:t></w:r><w:r><w:t>một</w:t></w:r></w:p>"
                               "<w:p><w:r><w:t>Trăm năm trong cõi</w:t><w:br/><w:t>người ta</w:t></w:r></w:p>")
    assert read_paragraphs(tmp_path / "a.docx") == ["Chương một", "Trăm năm trong cõi", "người ta"]


def test_txt_hard_wrapped_rejoins_inside_paragraph_and_legacy_font_converts(tmp_path):
    wrapped = "Trong lúc ấy, ông lão đứng dậy đi ra ngoài sân nhìn trời một\nlúc lâu rồi mới vào nhà.\n\nChương 2\n"
    (tmp_path / "a.txt").write_bytes(wrapped.encode("utf-8"))
    assert read_paragraphs(tmp_path / "a.txt") == [
        "Trong lúc ấy, ông lão đứng dậy đi ra ngoài sân nhìn trời một lúc lâu rồi mới vào nhà.", "Chương 2"]
    (tmp_path / "b.txt").write_bytes((TCVN3 + "\n").encode("cp1252"))
    text, encoding = _text(read_paragraphs(tmp_path / "b.txt") * 3)
    assert encoding == "tcvn3" and text.split("\n")[0] == UNICODE


def test_near_dup_groups_two_editions_keeps_epub_and_only_reports_a_volume_inside_a_collection():
    import random
    from pipeline.books.near_dup import find, shingle_sample
    rng = random.Random(7)
    vocab = [f"từ{i}" for i in range(3000)]
    text = lambda n: " ".join(rng.choice(vocab) for _ in range(n))  # noqa: E731
    novel, other, volume2 = text(20000), text(20000), text(20000)
    book = lambda path, ext, t: (path, (ext, 0.99, len(t)), shingle_sample(t))  # noqa: E731
    books = [book("a.pdf", "pdf", novel), book("a.epub", "epub", "Lời giới thiệu bản mới. " + novel),
             book("b.epub", "epub", other), book("set.epub", "epub", novel + " " + volume2)]
    copies, contained = find(books)
    assert copies == [["a.epub", "a.pdf"]]
    assert {(s, b) for s, b, _ in contained} >= {("a.epub", "set.epub")} and "b.epub" not in str(contained)
