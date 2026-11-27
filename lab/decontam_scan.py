"""
Which lab token shards contain documents that leak a frozen eval item? Re-tokenize those with
`python -m lab.data_prep --redo <source> ...` (data_prep drops matching documents).

  LLM_DATA_ROOT=E:/llm-data uv run --group lab python -m lab.decontam_scan [--workers 8]

Scans, per source, the documents data_prep tokenized: files in iter_docs order until the source's recorded
`bytes`, matched with pipeline.process.decontam against every frozen eval set (or the eval sets listed).
Sources that data_prep already filtered against all of those eval sets (their <source>.json lists them) are not
scanned: their matching documents were dropped before tokenization.
Report: LAB_DATA/decontam-scan.json (per source: docs scanned, contaminated docs, eval items hit). Not in TOKENS_DIR:
data_loader reads every *.json there as a source's token counts.
"""
import argparse
import json
from collections import Counter, deque
from concurrent.futures import ProcessPoolExecutor
from functools import cache
from itertools import islice

from lab import corpus
from pipeline.process.decontam import EvalIndex, frozen_evalsets

_EVALSETS = None  # set in each worker by _init


def _init(evalsets):
    global _EVALSETS
    _EVALSETS = evalsets


@cache
def _index():
    return EvalIndex(_EVALSETS)


def scan_file(path):
    """(bytes, docs, contaminated docs, Counter of eval item ids) for one parquet file."""
    n_bytes = docs = bad = 0
    hits = Counter()
    for _, text in corpus.iter_file_docs(path):
        docs += 1
        n_bytes += len(text.encode())
        found = _index().matches(text)
        if found:
            bad += 1
            hits.update(found)
    return n_bytes, docs, bad, hits


def scan_source(pool, workers, source_id, byte_cap):
    """Files go out in iter_docs order, at most `workers` in flight, and results come back in that order, so
    the scan stops near the file where data_prep's byte count was reached (canonical sources are far larger
    than what the lab tokenized: fineweb2_vie has 132 GB of parquet for 8 GB used)."""
    total = {"bytes": 0, "docs": 0, "contaminated_docs": 0}
    hits = Counter()
    files = iter(corpus.source_files(source_id))
    inflight = deque(pool.submit(scan_file, f) for f in islice(files, workers))
    while inflight and total["bytes"] < byte_cap:
        n_bytes, docs, bad, found = inflight.popleft().result()
        total["bytes"] += n_bytes
        total["docs"] += docs
        total["contaminated_docs"] += bad
        hits.update(found)
        inflight.extend(pool.submit(scan_file, f) for f in islice(files, 1))
    for future in inflight:
        future.cancel()
    return total | {"eval_items_hit": dict(sorted(hits.items()))}


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--evalsets", nargs="+", help="default: every frozen eval set")
    args = parser.parse_args()
    evalsets = tuple(args.evalsets) if args.evalsets else frozen_evalsets()
    stats = {s: json.loads(p.read_text(encoding="utf-8")) for s, p in
             ((p.name[:-5], p) for p in sorted(corpus.TOKENS_DIR.glob("*.json")) if p.name != "manifest.json"
              and not p.name.startswith("decontam"))}
    report = {"evalsets": list(evalsets), "sources": {}}
    with ProcessPoolExecutor(args.workers, initializer=_init, initargs=(evalsets,)) as pool:
        for source_id, counts in stats.items():
            if set(evalsets) <= set(counts.get("evalsets", [])):
                # data_prep already dropped matching documents when it tokenized this source
                result = {"filtered_at_tokenization": True, "contaminated_docs": 0, "eval_items_hit": {}}
                print(f"{source_id:28} filtered at tokenization against {', '.join(counts['evalsets'])}", flush=True)
            else:
                result = scan_source(pool, args.workers, source_id, counts["bytes"])
                print(f"{source_id:28} docs={result['docs']:>9} contaminated={result['contaminated_docs']:>6} "
                      f"items={len(result['eval_items_hit'])}", flush=True)
            report["sources"][source_id] = result
    (corpus.LAB_DATA / "decontam-scan.json").write_text(json.dumps(report, indent=1), encoding="utf-8")
    dirty = [s for s, r in report["sources"].items() if r["contaminated_docs"]]
    print("re-tokenize:", " ".join(dirty) if dirty else "nothing")


if __name__ == "__main__":
    main()
