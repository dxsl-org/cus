from pipeline.acquire.hplt3 import parse_hplt3_row
from pipeline.schema.document_record import validate


def test_parse_hplt3_row_valid_and_filtered():
    row = {
        "id": "abc123",
        "text": "Văn bản tiếng Việt thử nghiệm trích từ HPLT 3.0.",
        "u": "https://example.vn/bai-viet",
        "ts": "2017-06-29T15:42:29Z",
        "crawl_id": "CC-MAIN-2017-26",
        "cluster_size": 2,
        "filter": "keep",
        "doc_scores": [10, 10],
        "web-register": {"NA": 0.6},
    }
    rec = parse_hplt3_row(row, "10_1.jsonl.zst", 1, "raw/open_datasets/hplt3_vie_hq/10_1.jsonl.zst")
    assert validate(rec) == []
    assert rec["era"] == "pre_llm"
    assert rec["category"] == "tier_10"
    assert rec["license"] == "cc0-1.0"

    dropped = dict(row, filter="discard")
    assert parse_hplt3_row(dropped, "10_1.jsonl.zst", 2, "raw/x") is None
