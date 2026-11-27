from datetime import date, datetime

from pipeline.ingest.legal_open_export import _make_record, html_to_legal_text, parse_legal_date
from pipeline.schema.document_record import validate


def test_parse_legal_date_formats_and_bounds():
    bound = date(2026, 11, 19)
    assert parse_legal_date("29/01/2026", bound) == date(2026, 1, 29)
    assert parse_legal_date("2024-11-30", bound) == date(2024, 11, 30)
    assert parse_legal_date(datetime(2020, 5, 1, 0, 0), bound) == date(2020, 5, 1)
    assert parse_legal_date("01/01/2027", bound) is None
    assert parse_legal_date("invalid", bound) is None


def test_html_to_legal_text_full_doc_and_table():
    html = """<!DOCTYPE html><html><head><style>body{color:red}</style></head>
    <body><p><strong>Điều 1.</strong> Phạm vi.</p>
    <table><tr><td>Khoản 1</td><td>Nội dung</td></tr></table></body></html>"""
    text = html_to_legal_text(html)
    assert "color:red" not in text
    assert "Điều 1. Phạm vi." in text
    assert "Khoản 1 | Nội dung" in text


def test_legal_record_passes_contract():
    rec = _make_record(
        "hf_legal_vlsp2025", "repo#123", "Luật Điện lực quy định...", datetime(2026, 11, 19, 8, 0),
        title="Luật Điện lực", publish_date=date(2024, 11, 30), author="Quốc hội",
        extractor="hf_legal_vlsp2025@test", meta={"DocIdentity": "61/2024/QH15"},
    )
    assert validate(rec) == []
    assert rec["era"] == "post_llm"
    assert rec["license"] == "vn-not-protected"
