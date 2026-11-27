"""
Stage s4_clean: decontamination against frozen eval sets, then PII masking.

    uv run --group process python -m pipeline.process.stage_clean [sources...] [--workers 8]

Per document of s3_minhash: if it shares any 13-gram with an eval set's decontam list it is dropped
(and the eval item ids it touched are counted); otherwise emails, phones, ID / bank numbers and, in
court rulings, unmasked given names are masked (pii_vi) and text_sha256 is recomputed.
Output: PROCESSED_DIR/s4_clean/<source>/<origin>/ and per-source stats.json (no timestamps).
"""
import argparse
import json
import sys
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor, as_completed
from functools import cache

import pyarrow as pa
import pyarrow.parquet as pq

from pipeline.ingest.canonical_writer import publish, staging_dir
from pipeline.paths import PROCESSED_DIR
from pipeline.process.decontam import EvalIndex
from pipeline.process.pii_vi import mask
from pipeline.schema.document_record import ARROW_SCHEMA, text_sha256

IN_STAGE, STAGE = "s3_minhash", "s4_clean"
CHUNK_ROW_GROUPS = 5


@cache
def _index():
    return EvalIndex()  # every frozen eval set, so a new evalset-vN is filtered without code changes


def process_chunk(args):
    path, row_groups, out_path = args
    kept, stats = [], {"docs": 0, "kept": 0, "decontam_dropped": 0, "eval_hits": Counter(), "pii": Counter()}
    for batch in pq.ParquetFile(path).iter_batches(row_groups=row_groups):
        for row in batch.to_pylist():
            stats["docs"] += 1
            hits = _index().matches(row["text"])
            if hits:
                stats["decontam_dropped"] += 1
                stats["eval_hits"].update(hits)
                continue
            text, counts = mask(row["text"], row["source_type"])
            stats["pii"].update(counts)
            if counts:
                row = {**row, "text": text, "text_sha256": text_sha256(text)}
            kept.append(row)
    pq.write_table(pa.Table.from_pylist(kept, schema=ARROW_SCHEMA), out_path, compression="zstd",
                   row_group_size=2000)
    stats["kept"] = len(kept)
    return stats


def _chunks(source, root):
    for origin_dir in sorted(d for d in (root / IN_STAGE / source).iterdir() if d.is_dir()):
        chunks = []
        for f in sorted(origin_dir.glob("*.parquet")):
            groups = list(range(pq.ParquetFile(f).metadata.num_row_groups))
            chunks += [(f, groups[i:i + CHUNK_ROW_GROUPS]) for i in range(0, len(groups), CHUNK_ROW_GROUPS)]
        yield origin_dir.name, chunks


def run(sources, workers, root=PROCESSED_DIR):
    jobs, staging = [], {}
    for source in sorted(set(sources)):
        for origin, chunks in _chunks(source, root):
            tmp = staging[(source, origin)] = staging_dir(source, origin, root / STAGE)
            jobs += [(source, (f, groups, tmp / f"c{i:06d}.parquet")) for i, (f, groups) in enumerate(chunks)]
    totals = defaultdict(lambda: {"docs": 0, "kept": 0, "decontam_dropped": 0, "eval_hits": Counter(),
                                  "pii": Counter()})
    with ProcessPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(process_chunk, job): source for source, job in jobs}
        for future in as_completed(futures):
            stats, total = future.result(), totals[futures[future]]
            for key in ("docs", "kept", "decontam_dropped"):
                total[key] += stats[key]
            total["eval_hits"].update(stats["eval_hits"])
            total["pii"].update(stats["pii"])
    for (source, origin), tmp in staging.items():
        publish(tmp, source, origin, root / STAGE)
    for source, total in totals.items():
        out = {"stage": STAGE, "evalsets": list(_index().evalsets), "docs": total["docs"], "kept": total["kept"],
               "decontam_dropped": total["decontam_dropped"], "pii_masked": dict(sorted(total["pii"].items())),
               "eval_items_hit": dict(sorted(total["eval_hits"].items()))}
        (root / STAGE / source / "stats.json").write_text(json.dumps(out, indent=1, ensure_ascii=False),
                                                          encoding="utf-8")
    return {s: {k: v for k, v in t.items() if k != "eval_hits"} for s, t in totals.items()}


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("sources", nargs="*")
    parser.add_argument("--workers", type=int, default=8)
    args = parser.parse_args()
    sources = args.sources or sorted(d.name for d in (PROCESSED_DIR / IN_STAGE).iterdir() if d.is_dir())
    print(json.dumps(run(sources, args.workers), indent=1, ensure_ascii=False, default=dict))


if __name__ == "__main__":
    main()
