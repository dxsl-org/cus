"""
Stage s5_final: score q_edu, assign quality tiers, write the training-ready corpus.

    uv run --group process python -m pipeline.process.stage_final [sources...] [--workers 8]

Each s4_clean document gets q_edu (pipeline.quality.classifier, 0-5) and a tier:
tier_a >= TIER_A, tier_b >= TIER_B, else tier_c. Output PROCESSED_DIR/final/<tier>/<source>/<origin>/
with the DocumentRecord schema plus q_edu, and PROCESSED_DIR/final/stats.json: documents and
syllables per (lang, era, source_type, tier). Syllables, not tokens: the tokenizer is chosen in
phase 10, and the table is recomputed with it there.
"""
import argparse
import json
import sys
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from functools import cache

import pyarrow as pa
import pyarrow.parquet as pq

from pipeline.ingest.canonical_writer import publish, staging_dir
from pipeline.paths import PROCESSED_DIR
from pipeline.quality import classifier
from pipeline.schema.document_record import ARROW_SCHEMA

IN_STAGE, STAGE = "s4_clean", "final"
TIERS = ("tier_a", "tier_b", "tier_c")
TIER_A, TIER_B = 3.0, 2.0
FINAL_SCHEMA = ARROW_SCHEMA.append(pa.field("q_edu", pa.float32()))


@cache
def _model():
    return classifier.load()


def tier_of(q):
    return "tier_a" if q >= TIER_A else "tier_b" if q >= TIER_B else "tier_c"


def process_file(args):
    path, outs = args  # outs: {tier: output file path}
    table = pq.read_table(path)
    rows = table.to_pylist()
    scores = classifier.score(_model(), [r["text"] for r in rows]) if rows else []
    by_tier, counts = {t: [] for t in TIERS}, Counter()
    for row, q in zip(rows, scores):
        tier = tier_of(float(q))
        by_tier[tier].append({**row, "q_edu": float(q)})
        key = f"{row['lang']}|{row['era']}|{row['source_type']}|{tier}"
        counts[key + "|docs"] += 1
        counts[key + "|syllables"] += len(row["text"].split())
    for tier, out_rows in by_tier.items():
        if out_rows:
            pq.write_table(pa.Table.from_pylist(out_rows, schema=FINAL_SCHEMA), outs[tier], compression="zstd",
                           row_group_size=2000)
    return counts


def run(sources, workers, root=PROCESSED_DIR):
    jobs, staging = [], {}
    for source in sorted(set(sources)):
        for origin_dir in sorted(d for d in (root / IN_STAGE / source).iterdir() if d.is_dir()):
            for tier in TIERS:
                staging[(tier, source, origin_dir.name)] = staging_dir(source, origin_dir.name, root / STAGE / tier)
            for f in sorted(origin_dir.glob("*.parquet")):
                jobs.append((f, {t: staging[(t, source, origin_dir.name)] / f.name for t in TIERS}))
    totals = Counter()
    with ProcessPoolExecutor(max_workers=workers) as pool:
        for counts in pool.map(process_file, jobs):
            totals.update(counts)
    for (tier, source, origin), tmp in staging.items():
        publish(tmp, source, origin, root / STAGE / tier)
    table = {}
    for key, n in sorted(totals.items()):
        lang, era, source_type, tier, metric = key.split("|")
        table.setdefault(f"{lang}|{era}|{source_type}|{tier}", {})[metric] = n
    stats = {"stage": STAGE, "tier_thresholds": {"tier_a": TIER_A, "tier_b": TIER_B}, "unit": "syllables",
             "lang|era|source_type|tier": table}
    (root / STAGE / "stats.json").write_text(json.dumps(stats, indent=1), encoding="utf-8")
    return stats


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("sources", nargs="*")
    parser.add_argument("--workers", type=int, default=8)
    args = parser.parse_args()
    sources = args.sources or sorted(d.name for d in (PROCESSED_DIR / IN_STAGE).iterdir() if d.is_dir())
    print(json.dumps(run(sources, args.workers), indent=1))


if __name__ == "__main__":
    main()
