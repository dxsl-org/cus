from collections import defaultdict
from datetime import datetime

import pyarrow as pa
import pyarrow.parquet as pq

from pipeline.process import stage_clean
from pipeline.process.decontam import EvalIndex
from pipeline.schema.document_record import ARROW_SCHEMA, text_sha256

# Stock legal opening that thousands of rulings contain: sharing it alone is not contamination.
PREFIX = ("Theo Bộ luật Hình sự số 100/2015/QH13 đã được sửa đổi bổ sung bởi Luật số 12/2017/QH14 "
          "và Luật số 59/2024/QH15 ")
QUESTION = "người từ đủ 14 tuổi đến dưới 16 tuổi chuẩn bị phạm tội nào sau đây thì phải chịu trách nhiệm hình sự"


def _row(doc_id, text, source_type="news"):
    return {"doc_id": doc_id, "source_id": "src", "source_type": source_type, "url": None, "title": None,
            "text": text, "lang": "vie_Latn", "lang_score": 1.0, "publish_date": None, "publish_date_source": "none",
            "crawl_time": datetime(2024, 1, 1), "era": "unknown", "author": None, "category": None, "tags": [],
            "raw_ref": "x", "text_sha256": text_sha256(text), "license": "x", "usage_scope": "train_ok",
            "extractor": "x", "meta": None}


def test_leaked_question_dropped_but_shared_legal_phrase_kept(tmp_path, monkeypatch):
    index = EvalIndex.__new__(EvalIndex)
    index.owners, index.size = defaultdict(list), {}
    index.add("claude_drafts:abc", [PREFIX + QUESTION])
    monkeypatch.setattr(stage_clean, "_index", lambda: index)
    src = tmp_path / "in.parquet"
    rows = [_row("leak", "Câu hỏi ôn thi: " + PREFIX + QUESTION + "? Đáp án là 12 ngày."),
            _row("ruling", "Căn cứ " + PREFIX + "Tòa án tuyên bị cáo phạm tội trộm cắp tài sản.", "court"),
            _row("clean", "Mọi thắc mắc xin gọi 0912 345 678 để được hỗ trợ trong giờ hành chính.")]
    pq.write_table(pa.Table.from_pylist(rows, schema=ARROW_SCHEMA), src, row_group_size=10)

    stats = stage_clean.process_chunk((src, [0], tmp_path / "out.parquet"))

    out = pq.read_table(tmp_path / "out.parquet").to_pylist()
    assert [r["doc_id"] for r in out] == ["ruling", "clean"]
    assert "0xxx xxx xxx" in out[1]["text"] and out[1]["text_sha256"] == text_sha256(out[1]["text"])
    assert stats["decontam_dropped"] == 1 and stats["eval_hits"] == {"claude_drafts:abc": 1}


def test_item_shared_by_two_eval_sets_keeps_the_coverage_threshold():
    """evalset-v2 re-lists evalset-v1's items; indexing them twice must not double-count their 13-grams."""
    words = [f"từ{i}" for i in range(40)]  # 28 distinct 13-grams
    index = EvalIndex.__new__(EvalIndex)
    index.owners, index.size = defaultdict(list), {}
    for _ in range(2):
        index.add("vmlu:1", [" ".join(words)])
    assert not index.matches(" ".join(words[:21]))  # 9 of 28 grams: 32% coverage
    assert index.matches(" ".join(words[:30])) == {"vmlu:1"}  # 18 of 28: 64%
