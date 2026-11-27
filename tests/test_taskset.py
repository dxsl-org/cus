import json
import sqlite3

import pytest

from taskset import export, store
from taskset.verifiers import check, parse_choice, parse_number


def _sft(question="Thủ đô của Việt Nam là gì?", answer="Hà Nội."):
    return {"domain": "culture_history", "skills": ["recall"], "lang": "vi",
            "messages": [{"role": "user", "content": question}, {"role": "assistant", "content": answer}]}


def _rl(question="Tính 1.000,5 + 0,5"):
    return {"domain": "math", "skills": ["calculation"], "lang": "vi",
            "messages": [{"role": "user", "content": question}], "reference_answer": "1.001",
            "verifier": {"type": "numeric"}}


@pytest.fixture
def con(tmp_path):
    con = store.connect(tmp_path / "t.db")
    store.add_source(con, "own", "internal", "train_ok")
    store.add_source(con, "bench", "unknown", "blocked")
    return con


def _status(con, item_id):
    return con.execute("SELECT status FROM items WHERE id = ?", (item_id,)).fetchone()[0]


def test_db_rejects_skipping_review(con):
    item = store.create_item(con, "sft", "own", _sft(), "human:anh", "anh")
    with pytest.raises(sqlite3.IntegrityError):
        con.execute("UPDATE items SET status = 'approved' WHERE id = ?", (item,))
    store.submit(con, item, "anh")
    with pytest.raises(sqlite3.IntegrityError, match="needs a review"):
        con.execute("UPDATE items SET status = 'approved' WHERE id = ?", (item,))
    store.review(con, item, "anh", "dropped")
    with pytest.raises(sqlite3.IntegrityError, match="illegal status transition"):
        con.execute("UPDATE items SET status = 'pending' WHERE id = ?", (item,))


def test_versions_and_split_are_immutable(con):
    item = store.create_item(con, "sft", "own", _sft(), "human:anh", "anh", split="train")
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        con.execute("UPDATE item_versions SET content_json = '{}' WHERE item_id = ?", (item,))
    with pytest.raises(sqlite3.IntegrityError, match="fixed"):
        con.execute("UPDATE items SET split = 'test' WHERE id = ?", (item,))


def test_editing_approved_item_requires_new_review(con):
    item = store.create_item(con, "sft", "own", _sft(), "human:anh", "anh")
    store.submit(con, item, "anh")
    store.review(con, item, "anh", "approved")
    store.edit_content(con, item, _sft(answer="Thủ đô là Hà Nội."), "human:anh", "anh")
    assert _status(con, item) == "pending"
    store.review(con, item, "lan", "corrected", corrected_content=_sft(answer="Hà Nội"))
    assert _status(con, item) == "corrected"
    assert store.current_content(con, item)["messages"][1]["content"] == "Hà Nội"


def test_rl_verifiable_needs_failing_negative_before_approval(con):
    item = store.create_item(con, "rl_verifiable", "own", _rl(), "human:anh", "anh")
    store.submit(con, item, "anh")
    assert store.run_verifier(con, item, "Kết quả là 1001", "reference")[0]
    with pytest.raises(sqlite3.IntegrityError, match="failing negative"):
        store.review(con, item, "anh", "approved")
    assert store.run_verifier(con, item, "1000", "negative")[0] is False
    store.review(con, item, "anh", "approved")
    assert _status(con, item) == "approved"


def test_eval_and_restricted_sources_never_enter_training_splits(con):
    with pytest.raises(ValueError):
        store.create_item(con, "sft", "bench", _sft(), "import:bench", "import", split="train")
    mcq = {"domain": "general", "lang": "vi", "messages": [{"role": "user", "content": "1+1?"}],
           "choices": ["1", "2"], "answer_key": "B"}
    with pytest.raises(ValueError):
        store.create_item(con, "eval_mcq", "own", mcq, "human:anh", "anh", split="dev")


@pytest.mark.parametrize("text, value", [
    ("1.000,5", 1000.5), ("1,000.5", 1000.5), ("1.000", 1000.0), ("1,000", 1000.0), ("3,14", 3.14),
    ("0.125", 0.125), ("1.234.567", 1234567.0), ("Đáp số: -2,5 kg", -2.5), ("khoảng 12.", 12.0),
])
def test_parse_number_reads_vietnamese_and_english(text, value):
    assert parse_number(text) == pytest.approx(value)


@pytest.mark.parametrize("text, label", [("B", "B"), ("(C) vì...", "C"), ("Đáp án đúng là D.", "D"),
                                         ("Answer: A", "A"), ("Không biết", None)])
def test_parse_choice(text, label):
    assert parse_choice(text) == label


def test_unit_test_verifier_runs_isolated_with_timeout():
    spec = {"type": "unit_test", "spec": {"language": "python", "test": "assert add(2, 3) == 5", "timeout": 5}}
    assert check(spec, "def add(a, b):\n    return a + b", {})[0]
    assert not check(spec, "def add(a, b):\n    return a - b", {})[0]
    net = {"type": "unit_test", "spec": {"language": "python", "timeout": 5,
                                         "test": "import socket\nsocket.create_connection(('example.com', 80))"}}
    assert "network disabled" in check(net, "", {})[1]
    slow = {"type": "unit_test", "spec": {"language": "python", "test": "while True: pass", "timeout": 1}}
    assert check(slow, "", {}) == (False, "timeout after 1s")


def test_export_contains_only_accepted_items_and_is_deterministic(con, tmp_path):
    approved = store.create_item(con, "sft", "own", _sft("Câu 1?"), "human:anh", "anh", split="train")
    pending = store.create_item(con, "sft", "own", _sft("Câu 2?"), "human:anh", "anh", split="train")
    flagged = store.create_item(con, "sft", "own", _sft("Câu 3?"), "human:anh", "anh", split="train")
    auto_test = store.create_item(con, "rl_verifiable", "own", _rl("Câu 4?"), "model:t@1", "t", split="test")
    for item in (approved, pending, flagged, auto_test):
        store.submit(con, item, "anh")
    store.review(con, approved, "anh", "approved")
    store.review(con, flagged, "anh", "approved")
    store.set_flags(con, flagged, ["pii"], "anh")
    store.run_verifier(con, auto_test, "1001", "reference")
    store.run_verifier(con, auto_test, "7", "negative")
    store.mark_auto(con, auto_test)

    first = export.export(con, tmp_path / "out")
    assert export.export(con, tmp_path / "out") == first
    train = [json.loads(x) for x in (first / "train.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [x["id"] for x in train] == [approved]
    assert (first / "test.jsonl").read_text(encoding="utf-8") == ""  # auto never lands in test
    manifest = json.loads((first / "MANIFEST.json").read_text(encoding="utf-8"))
    assert manifest["counts"] == {"train": 1, "dev": 0, "test": 0, "holdout": 0}
