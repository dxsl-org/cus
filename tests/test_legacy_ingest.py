import json
from datetime import date, datetime

from pipeline.ingest.date_normalizer import parse_vi_date, resolve_publish_date
from pipeline.ingest.legacy_record import build_record
from pipeline.schema.document_record import validate

NEWS = {"id": "tuoitre_vn", "source_type": "news", "license": "copyrighted", "usage_scope": "train_ok"}
CRAWLED = "2026-03-06 10:00:00"


def _row(**overrides):
    row = {"id": 1, "url": "https://tuoitre.vn/a.htm", "title": "T", "author": None, "content": "Nội dung.",
           "tags": None, "category": None, "comments": None, "created_at": CRAWLED, "metadata": None}
    row.update(overrides)
    return row


def test_observed_legacy_date_formats():
    cases = {
        "Thứ năm, 12/03/2026 06:14": date(2026, 3, 12),
        "18 Tháng mười 2023 lúc 11:01 AM": date(2023, 10, 18),
        "30 Tháng mười một 2019 lúc 9:00": date(2019, 11, 30),
        "22:12 ngày 11/3/2012": date(2012, 3, 11),
        "17:08 | Thứ hai, 05/05/20140": date(2014, 5, 5),   # nguoidothi glues a digit to the year
        "12/2/26 lúc 18:57": date(2026, 2, 12),              # XenForo 2-digit year
        "13/01/2026 07:55Chia sẻ": date(2026, 1, 13),
        "**SỐ 138/SL NGÀY 19 THÁNG 9 NĂM 1950**": date(1950, 9, 19),
        "2010-01-31T02:24:00+0700": date(2010, 1, 31),
    }
    for text, expected in cases.items():
        assert parse_vi_date(text, date(2026, 11, 13)) == expected, text


def test_url_date_beats_scraped_crawl_date_field():
    # tuoitre: migrated metadata.date holds the crawl day; the article id holds the real date.
    meta = {"date": "2026-03-03", "link": "https://tuoitre.vn/co-kieng-20230329110615464.htm"}
    assert resolve_publish_date("tuoitre_vn", meta, crawled_at=datetime(2026, 3, 6)) == (date(2023, 3, 29), "url")
    # 16-digit ids exist too (yyyymmddHHMMSS + 2).
    short_id = {"date": "2026-03-03", "link": "https://tuoitre.vn/melano-ra-mat-2024032216315385.htm"}
    assert resolve_publish_date("tuoitre_vn", short_id, crawled_at=datetime(2026, 3, 6)) == (date(2024, 3, 22), "url")


def test_fields_known_to_hold_crawl_date_are_ignored():
    # baovanhoa scraped the site header's "today"; tuoitre's migrated `date` is the crawl day.
    assert resolve_publish_date("baovanhoa_vn", {"published_date": "Thứ năm, 12/03/2026 06:16"},
                                crawled_at=datetime(2026, 3, 13)) == (None, "none")
    assert resolve_publish_date("tuoitre_vn", {"date": "2026-03-03"}, crawled_at=datetime(2026, 3, 6)) == (None, "none")


def test_dates_after_crawl_and_news_body_dates_are_rejected():
    assert resolve_publish_date("plo_vn", {"publish_date": "2026-07-01"}, crawled_at=datetime(2026, 3, 13)) == (None, "none")
    assert resolve_publish_date("plo_vn", {}, content_head="Ngày 19/8/1945 cách mạng...") == (None, "none")
    assert resolve_publish_date("vbpl_vn", {}, content_head="SỐ 138/SL NGÀY 19 THÁNG 9 NĂM 1950") == (date(1950, 9, 19), "content")


def test_local_path_url_replaced_by_metadata_link():
    meta = {"link": "https://tuoitre.vn/bai-20240618205945975.htm?utm_source=x"}
    record, skip = build_record(NEWS, _row(url=r"e:\Data\tuoitre.vn\x.md", metadata=json.dumps(meta)), "tuoitre_vn.db")
    assert skip is None and validate(record) == []
    assert record["url"] == "https://tuoitre.vn/bai-20240618205945975.htm"
    assert record["publish_date"] == date(2024, 6, 18) and record["era"] == "post_llm"
    assert json.loads(record["meta"])["legacy_url"] == r"e:\Data\tuoitre.vn\x.md"


def test_empty_content_skipped_and_vbpl_junk_stripped():
    assert build_record(NEWS, _row(content="  \n"), "x.db") == (None, "empty_content")
    vbpl = {**NEWS, "id": "vbpl_vn", "source_type": "legal"}
    record, _ = build_record(vbpl, _row(content="Are You suprised ?**CHỈ THỊ**"), "vbpl.vn.db")
    assert record["text"] == "**CHỈ THỊ**"


def test_split_diacritics_flagged_for_reextraction():
    broken = "B n án s :\nả\nố\nT I THÀNH PH\nẠ\nỐ\nỒ\nC NG HÒA\nộ\n"
    court = {**NEWS, "id": "toaan_gov_vn", "source_type": "court"}
    record, _ = build_record(court, _row(content=broken), "toaan_gov_vn.db")
    assert json.loads(record["meta"])["text_issue"] == "broken_diacritics"
    assert record["usage_scope"] == "blocked"


def test_recrawled_patch_fills_missing_date_only():
    record, _ = build_record(NEWS, _row(url="https://plo.vn/a-post1.html"), "plo.db")
    patches = {record["doc_id"]: {"publish_date": date(2019, 5, 1), "tag": "recrawl", "detail": None}}
    patched, _ = build_record(NEWS, _row(url="https://plo.vn/a-post1.html"), "plo.db", patches)
    assert (patched["publish_date"], patched["publish_date_source"], patched["era"]) == (date(2019, 5, 1), "recrawl", "pre_llm")
    too_late = {record["doc_id"]: {"publish_date": date(2026, 4, 1), "tag": "recrawl", "detail": None}}
    assert build_record(NEWS, _row(url="https://plo.vn/a-post1.html"), "plo.db", too_late)[0]["publish_date"] is None
