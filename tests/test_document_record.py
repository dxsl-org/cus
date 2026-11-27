import json
import unicodedata
from datetime import date, datetime

from pipeline.schema.document_record import (
    canonical_url, era_for, make_doc_id, text_sha256, validate,
)
from pipeline.schema.sources_manifest import load, validate as validate_sources


def _record(**overrides):
    text = "Hà Nội mùa thu."
    record = {
        "doc_id": "x", "source_id": "tuoitre_vn", "source_type": "news", "text": text,
        "publish_date": date(2021, 5, 1), "publish_date_source": "url",
        "crawl_time": datetime(2026, 3, 1, 10, 0), "era": "pre_llm",
        "text_sha256": text_sha256(text), "license": "copyrighted", "usage_scope": "train_ok",
        "extractor": "legacy-sqlite@1", "meta": json.dumps({"link": "https://tuoitre.vn/a.htm"}),
    }
    record.update(overrides)
    return record


def test_valid_record_passes():
    assert validate(_record()) == []


def test_rejects_decomposed_vietnamese_text():
    nfd = unicodedata.normalize("NFD", "Hà Nội mùa thu.")
    errors = validate(_record(text=nfd, text_sha256=text_sha256(nfd)))
    assert "text is not NFC" in errors


def test_rejects_publish_date_after_crawl():
    errors = validate(_record(publish_date=date(2026, 3, 2), era="post_llm"))
    assert any("after crawl_time" in e for e in errors)


def test_era_must_follow_cutoff():
    assert era_for(date(2022, 11, 30)) == "pre_llm"
    assert era_for(date(2022, 12, 1)) == "post_llm"
    assert era_for(None) == "unknown"
    # Unknown publish date but captured before the cutoff (Common Crawl 2013): written pre-LLM.
    assert era_for(None, datetime(2013, 12, 9)) == "pre_llm"
    assert era_for(None, datetime(2023, 6, 1)) == "unknown"
    assert any("era" in e for e in validate(_record(publish_date=date(2023, 1, 1))))


def test_missing_date_requires_none_source_and_unknown_era():
    assert validate(_record(publish_date=None, publish_date_source="none", era="unknown")) == []
    assert validate(_record(publish_date=None, era="unknown"))  # source 'url' but no date


def test_rejects_leaked_credentials_in_meta():
    errors = validate(_record(meta=json.dumps({"Cookie": "sid=1"})))
    assert any("sensitive" in e for e in errors)


def test_doc_id_stable_across_tracking_params():
    a = make_doc_id("plo_vn", "https://PLO.vn/bai-viet-post1.html?utm_source=fb#top")
    b = make_doc_id("plo_vn", "https://plo.vn/bai-viet-post1.html")
    assert a == b
    assert canonical_url("https://vbpl.vn/x.aspx?ItemID=41&fbclid=z") == "https://vbpl.vn/x.aspx?ItemID=41"


def test_sources_manifest_claims_every_legacy_source():
    entries = load()
    assert validate_sources(entries, ["tuoitre_vn"], ["tuoitre_vn.db"]) == []
    errors = validate_sources(entries, ["not_registered"], [])
    assert errors == ["legacy config id 'not_registered' claimed 0 times"]
