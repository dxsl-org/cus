"""
Sheet of documents for a person to score 0-5 with the same rubric as the LLM labeller; it validates
the q_edu classifier (phase 6: ~300 hand labels) and the LLM labels themselves.

    uv run python -m pipeline.quality.hand_label_sheet [--n 300]

Documents come only from the classifier's holdout (classifier._is_holdout), so the check never
scores text the classifier was trained on. Same split as the labelling sample (OPEN_SHARE from the
open web dumps the classifier will mostly score), round-robin over sources within each part.
Output: DATA_ROOT/quality/hand-labels.csv with columns doc_id, source, text, diem (to fill), ghi_chu.
Score it with `python -m pipeline.quality.classifier check <csv>`.
"""
import argparse
import csv
import json
import sys
from collections import defaultdict

from pipeline.paths import DATA_ROOT
from pipeline.quality.classifier import DOC_CHARS, _is_holdout
from pipeline.quality.sample_for_labels import OPEN_DATASETS, OPEN_SHARE

COLUMNS = ["doc_id", "source", "text", "diem", "ghi_chu"]


def _spread(by_source, sources, n):
    """Round-robin over the given sources (documents in doc_id order) until n are taken."""
    queues = {s: sorted(by_source[s], key=lambda d: d["doc_id"]) for s in sorted(sources)}
    out = []
    while len(out) < n and any(queues.values()):
        for s in queues:
            if queues[s] and len(out) < n:
                out.append(queues[s].pop(0))
    return out


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("--n", type=int, default=300)
    args = parser.parse_args()
    by_source = defaultdict(list)
    for doc in map(json.loads, open(DATA_ROOT / "quality" / "label_sample.jsonl", encoding="utf-8")):
        if _is_holdout(doc["doc_id"]):
            by_source[doc["source_id"]].append(doc)
    picked = _spread(by_source, [s for s in by_source if s in OPEN_DATASETS], int(args.n * OPEN_SHARE))
    picked += _spread(by_source, [s for s in by_source if s not in OPEN_DATASETS], args.n - len(picked))
    out = DATA_ROOT / "quality" / "hand-labels.csv"
    with open(out, "w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, COLUMNS)
        writer.writeheader()
        for d in picked:
            writer.writerow({"doc_id": d["doc_id"], "source": d["source_id"], "text": d["text"][:DOC_CHARS],
                             "diem": "", "ghi_chu": ""})
    print(out, len(picked))


if __name__ == "__main__":
    main()
