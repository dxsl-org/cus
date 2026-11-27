"""
Sample documents for LLM quality labelling (phase 6 step 7).

    uv run --group process python -m pipeline.quality.sample_for_labels [--total 50000]

The classifier trained on these labels will score every document that survives s1-s4, most of
them from open web dumps, so the sample is drawn from canonical documents that pass the s1
filters (same normalize/lid/filters code), split OPEN_SHARE across the open web datasets and the
rest across our own sources (equal share per source, capped by what the source has).
Selection takes the smallest doc_ids (sha1 hex, i.e. uniform), so re-running gives the same sample.
Output: DATA_ROOT/quality/label_sample.jsonl  {doc_id, source_id, source_type, text (<= 3000 chars)}.
"""
import argparse
import json
import sys
from concurrent.futures import ProcessPoolExecutor

import pyarrow.compute as pc
import pyarrow.dataset as ds

from pipeline.paths import CANONICAL_ROOTS, DATA_ROOT, canonical_source_dir
from pipeline.process.stage_filter import process_document

OPEN_DATASETS = ("fineweb2_vie", "culturax_vi", "hplt2_vie")
OPEN_SHARE = 0.6
TEXT_CHARS = 3000
OVERSAMPLE = 3  # candidates per wanted document: some fail the s1 filters


def _candidates(source, n):
    """
    About n*OVERSAMPLE train_ok documents with the smallest doc_ids. A doc_id prefix is a uniform
    random subset, and filtering on it keeps 100M-row sources out of memory.
    """
    data = ds.dataset(canonical_source_dir(source), format="parquet", exclude_invalid_files=True)
    want, total, prefix = n * OVERSAMPLE, data.count_rows(), ""
    while len(prefix) < 6 and total / 16 ** (len(prefix) + 1) >= want:
        prefix += "0"
    scope = (pc.field("usage_scope") == "train_ok") & pc.starts_with(pc.field("doc_id"), prefix)
    ids = sorted(data.to_table(columns=["doc_id"], filter=scope)["doc_id"].to_pylist())[:want]
    rows = data.to_table(filter=pc.field("doc_id").isin(ids)).to_pylist()
    return sorted(rows, key=lambda r: r["doc_id"])


def sample_source(args):
    source, n = args
    out = []
    for row in _candidates(source, n):
        reason, doc = process_document(row)
        if reason is None:
            out.append({"doc_id": doc["doc_id"], "source_id": source, "source_type": doc["source_type"],
                        "text": doc["text"][:TEXT_CHARS]})
            if len(out) == n:
                break
    return source, out


def _own_sources():
    names = {d.name for r in CANONICAL_ROOTS if r.is_dir() for d in r.iterdir() if d.is_dir()}
    return sorted(s for s in names - set(OPEN_DATASETS) if canonical_source_dir(s) and any(
        canonical_source_dir(s).rglob("*.parquet")))


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("--total", type=int, default=50_000)
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args()
    open_n = int(args.total * OPEN_SHARE) // len(OPEN_DATASETS)
    own = _own_sources()
    own_n = (args.total - open_n * len(OPEN_DATASETS)) // len(own)
    plan = [(s, open_n) for s in OPEN_DATASETS] + [(s, own_n) for s in own]
    out_path = DATA_ROOT / "quality" / "label_sample.jsonl"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    total = 0
    with ProcessPoolExecutor(max_workers=args.workers) as pool, \
            open(out_path, "w", encoding="utf-8", newline="\n") as fh:
        for source, rows in pool.map(sample_source, plan):
            total += len(rows)
            fh.writelines(json.dumps(r, ensure_ascii=False) + "\n" for r in rows)
            print(f"{source}: {len(rows)}", flush=True)
    print(out_path, total)


if __name__ == "__main__":
    main()
