"""
Hand-review sample for the s1 filters (phase 6 success criterion: >= 90% of rejects rightly
rejected, >= 95% of kept documents readable).

    uv run python -m pipeline.process.review_sample [--per-status 500] [sources...]
    uv run python -m pipeline.process.review_sample --score <filled csv>

Draws PER_STATUS kept and PER_STATUS rejected documents, spread evenly over sources (and over
reasons within the rejected sample) by a salted hash, into PROCESSED_DIR/review/s1-<config>.csv
(UTF-8 BOM, opens in Excel). The reviewer fills `dung` with 1 (decision right) or 0 (wrong).
"""
import argparse
import csv
import hashlib
import json
import sys
from collections import defaultdict

import pyarrow.dataset as ds

from pipeline.paths import PROCESSED_DIR
from pipeline.process.stage_filter import REJECTED, STAGE, config_hash

SALT = "s1-review/v1"
EXCERPT = 1500
POOL = 2000  # candidates read per source and stage before spreading
COLUMNS = ["status", "source", "reason", "doc_id", "excerpt", "dung", "ghi_chu"]


def _rank(doc_id):
    return hashlib.sha256(f"{SALT}:{doc_id}".encode()).hexdigest()


def _spread(groups, total):
    """Round-robin over groups (each sorted by hash) until `total` items are taken."""
    out, iters = [], {k: iter(v) for k, v in sorted(groups.items())}
    while len(out) < total and iters:
        for key in list(iters):
            item = next(iters[key], None)
            if item is None:
                del iters[key]
            elif len(out) < total:
                out.append((key, item))
    return out


def _rows(stage, source, columns):
    files = sorted((PROCESSED_DIR / stage / source).rglob("*.parquet"))
    if not files:  # e.g. a source with no rejected documents
        return []
    rows = ds.dataset(files, format="parquet").to_table(columns=columns).to_pylist()
    return sorted(rows, key=lambda r: _rank(r["doc_id"]))[:POOL * 10]


def sample(sources, per_status):
    kept = {s: _rows(STAGE, s, ["doc_id", "text"])[:POOL] for s in sources}
    rejected = defaultdict(list)
    for s in sources:
        for r in _rows(REJECTED, s, ["doc_id", "reason", "text_head"]):
            rejected[(s, r["reason"].split(":")[0])].append(r)
    out = PROCESSED_DIR / "review" / f"s1-{config_hash()}.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, COLUMNS)
        writer.writeheader()
        for source, r in _spread(kept, per_status):
            writer.writerow({"status": "giu", "source": source, "reason": "", "doc_id": r["doc_id"],
                             "excerpt": r["text"][:EXCERPT], "dung": "", "ghi_chu": ""})
        for (source, _), r in _spread(rejected, per_status):
            writer.writerow({"status": "loai", "source": source, "reason": r["reason"], "doc_id": r["doc_id"],
                             "excerpt": r["text_head"][:EXCERPT], "dung": "", "ghi_chu": ""})
    return out


def _reject_counts():
    """Real number of rejected documents per reason, from every source's s1 stats.json."""
    counts = defaultdict(int)
    for stats in (PROCESSED_DIR / STAGE).glob("*/stats.json"):
        for reason, n in json.loads(stats.read_text(encoding="utf-8"))["rejected_by_reason"].items():
            counts[reason] += n
    return counts


def score(path):
    """
    Share of right decisions. Kept documents were sampled evenly over sources; rejected ones evenly over
    reasons, so the reject figure is re-weighted by how many documents each reason really removed
    (a rare rule must not count as much as too_short). Per-reason figures show which rule to retune.
    """
    kept, by_reason = [], defaultdict(list)
    with open(path, encoding="utf-8-sig", newline="") as fh:
        for row in csv.DictReader(fh):
            mark = row["dung"].strip()
            if mark not in ("0", "1"):
                continue
            if row["status"] == "giu":
                kept.append(mark == "1")
            else:
                by_reason[row["reason"].split(":")[0]].append(mark == "1")
    weights = _reject_counts()
    total = sum(weights[r] for r in by_reason)
    weighted = sum(weights[r] * sum(v) / len(v) for r, v in by_reason.items()) / total if total else None
    return {"giu": {"n": len(kept), "ty_le_dung": round(sum(kept) / len(kept), 3) if kept else None},
            "loai": {"n": sum(map(len, by_reason.values())), "ty_le_dung_co_trong_so": round(weighted, 3)
                     if weighted is not None else None,
                     "theo_ly_do": {r: f"{sum(v)}/{len(v)}" for r, v in sorted(by_reason.items())}}}


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("sources", nargs="*")
    parser.add_argument("--per-status", type=int, default=500)
    parser.add_argument("--score", metavar="CSV")
    args = parser.parse_args()
    if args.score:
        print(json.dumps(score(args.score), ensure_ascii=False))
        return
    sources = args.sources or sorted(d.name for d in (PROCESSED_DIR / STAGE).iterdir() if d.is_dir())
    print(sample(sources, args.per_status))


if __name__ == "__main__":
    main()
