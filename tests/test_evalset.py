import json

import pytest

from eval import build_evalset, scorers
from taskset import store


def test_wilson_interval_matches_reference_values():
    lo, hi = scorers.wilson(80, 100)  # hand-computed with z = 1.96
    assert (lo, hi) == pytest.approx((0.711169, 0.866634), abs=1e-5)
    assert scorers.wilson(0, 0) == (0.0, 0.0)
    assert scorers.wilson(10, 10)[1] == 1.0


def test_generation_scoring_uses_item_verifiers():
    mcq = {"kind": "eval_mcq", "lang": "vi", "choices": ["x", "y"], "answer_key": "B",
           "messages": [{"role": "user", "content": "?"}]}
    assert scorers.score_generation(mcq, "Đáp án: B")[0]
    code = {"kind": "eval_code", "verifier": {"type": "unit_test", "spec": {
        "language": "python", "test": "assert f() == 1", "timeout": 5}}}
    reply = "Here:\n```python\ndef f():\n    return 1\n```\nDone."
    assert scorers.score_generation(code, reply)[0]
    open_item = {"kind": "eval_open", "reference_answer": ["Hà Nội"],
                 "verifier": {"type": "exact", "normalize": ["nfc", "lower", "strip_punct"]}}
    assert scorers.score_generation(open_item, "Đáp án: Hà Nội.\nVì ...")[0]


def test_summary_splits_multilingual_group_by_language():
    rows = [{"group": "m", "lang": "vi", "correct": True}, {"group": "m", "lang": "en", "correct": False},
            {"group": "r", "lang": "vi", "correct": True}]
    summary = scorers.summarize(rows)
    assert set(summary) == {"m", "m:en", "m:vi", "r"}
    assert summary["m"]["n"] == 2 and summary["m:vi"]["acc"] == 1.0


@pytest.fixture
def con(tmp_path):
    con = store.connect(tmp_path / "t.db")
    store.add_source(con, "bench", "unknown", "blocked")
    for n in range(6):
        item = f"bench:{n}"
        store.create_item(con, "eval_mcq", "bench", {
            "domain": "general", "lang": "vi", "messages": [{"role": "user", "content": f"Câu {n}?"}],
            "choices": ["a", "b"], "answer_key": "A", "verifier": {"type": "choice"}}, "import:bench", "i",
            item_id=item)
        store.submit(con, item, "i")
        if n < 4:
            store.review(con, item, "anh", "approved")
    return con


def test_freeze_takes_only_reviewed_items_and_never_rebuilds(con, tmp_path, monkeypatch):
    decl = {"name": "evalset-test", "sample_salt": "s", "groups": [{"name": "g", "sources": ["bench"], "sample": 3}]}
    monkeypatch.setattr(build_evalset, "load", lambda name: decl)
    monkeypatch.setattr(build_evalset, "DATA_ROOT", tmp_path)
    monkeypatch.setattr(build_evalset, "EVALSET_DIR", tmp_path)
    (tmp_path / "evalset-test.yaml").write_text("x", encoding="utf-8")
    out_dir, counts = build_evalset.build(con, "evalset-test")
    items = [json.loads(x) for x in (out_dir / "items.jsonl").read_text(encoding="utf-8").splitlines()]
    assert counts == {"g": 3}
    assert {i["id"] for i in items} <= {f"bench:{n}" for n in range(4)}
    with pytest.raises(SystemExit, match="never rebuilt"):
        build_evalset.build(con, "evalset-test")
