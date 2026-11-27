"""
Stage s1_filter: canonical -> normalized, language-identified, heuristically filtered documents.

    uv run --group process python -m pipeline.process.stage_filter [sources...] [--workers 8]

Per (source, origin) of the canonical store:
  PROCESSED_DIR/s1_filter/<source>/<origin>/*.parquet     kept DocumentRecords (text normalized,
                                                          text_sha256 recomputed, lang/lang_score set)
  PROCESSED_DIR/s1_rejected/<source>/<origin>/*.parquet   doc_id, reason, metrics, first 2000 chars
  PROCESSED_DIR/s1_filter/<source>/stats.json             counts and syllables in/out by reason
Both directories are published atomically when the whole (source, origin) is done. stats.json is a
pure function of input + code + thresholds (no timestamps), so a re-run must reproduce it.
`blocked` documents are counted and dropped without being written anywhere.
"""
import argparse
import hashlib
import json
import sys
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor, as_completed

import pyarrow as pa
import pyarrow.parquet as pq

from pipeline.ingest.canonical_writer import ShardWriter, publish, staging_dir
from pipeline.paths import CANONICAL_ROOTS, PROCESSED_DIR, canonical_source_dir
from pipeline.process import filters_vi
from pipeline.process.lid import GLOTLID_REVISION, identify
from pipeline.process.normalize import normalize
from pipeline.schema.document_record import text_sha256

STAGE, REJECTED = "s1_filter", "s1_rejected"
KEEP_LANGS, MIN_LANG_SCORE = frozenset({"vie_Latn"}), 0.65
REJECT_HEAD_CHARS = 2000
REJECT_SCHEMA = pa.schema([("doc_id", pa.string()), ("source_id", pa.string()), ("reason", pa.string()),
                           ("lang", pa.string()), ("lang_score", pa.float32()), ("metrics", pa.string()),
                           ("text_head", pa.large_string())])
BATCH = 2000
CHUNK_ROW_GROUPS = 5  # ~10k documents per job: legacy sources are 1-2 big files


def config_hash():
    blob = json.dumps({"thresholds": filters_vi.THRESHOLDS, "legal": filters_vi.LEGAL_OVERRIDES,
                       "legal_sources": sorted(filters_vi.LEGAL_SOURCES), "keep": sorted(KEEP_LANGS), "min_lang": MIN_LANG_SCORE,
                       "glotlid": GLOTLID_REVISION}, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


def process_document(row):
    """(reason | None, output row). Output is the updated record when kept, a reject row otherwise."""
    if row["usage_scope"] == "blocked":
        return "blocked", None
    text = normalize(row["text"], row["source_id"])
    lang, score = identify(text)
    if lang not in KEEP_LANGS or score < MIN_LANG_SCORE:
        reason, metrics = f"lang:{lang}" if lang not in KEEP_LANGS else "lang_score", {}
    else:
        reason, metrics = filters_vi.check(text, filters_vi.thresholds_for(row["source_id"], row["source_type"]))
    if reason is None:
        return None, {**row, "text": text, "text_sha256": text_sha256(text), "lang": lang, "lang_score": score}
    return reason, {"doc_id": row["doc_id"], "source_id": row["source_id"], "reason": reason, "lang": lang,
                    "lang_score": score, "metrics": json.dumps(metrics, sort_keys=True),
                    "text_head": text[:REJECT_HEAD_CHARS]}


def process_chunk(args):
    """Row groups of one canonical file -> kept shards + one rejected file in the staging dirs; returns stats."""
    path, row_groups, source, origin, keep_tmp, reject_tmp, prefix = args
    writer = ShardWriter(source, origin, root=PROCESSED_DIR / STAGE, tmp_dir=keep_tmp, shard_prefix=prefix)
    rejects, stats = [], {"docs": 0, "kept": 0, "syllables_in": 0, "syllables_out": 0, "reasons": Counter()}
    for batch in pq.ParquetFile(path).iter_batches(batch_size=BATCH, row_groups=row_groups):
        for row in batch.to_pylist():
            stats["docs"] += 1
            stats["syllables_in"] += len((row["text"] or "").split())
            reason, out = process_document(row)
            if reason is None:
                writer.write(out)
                stats["kept"] += 1
                stats["syllables_out"] += len(out["text"].split())
            else:
                stats["reasons"][reason.split(":")[0] if reason.startswith("lang:") else reason] += 1
                if out is not None:
                    rejects.append(out)
    writer.close()
    if rejects:
        pq.write_table(pa.Table.from_pylist(rejects, schema=REJECT_SCHEMA), reject_tmp / f"{prefix}.parquet",
                       compression="zstd")
    return stats


def _inputs(source):
    """(origin, [(file, [row group indexes])]) with at most CHUNK_ROW_GROUPS row groups per chunk."""
    root = canonical_source_dir(source)
    for origin_dir in sorted(d for d in root.iterdir() if d.is_dir() and not d.name.startswith(".")):
        chunks = []
        for f in sorted(origin_dir.glob("*.parquet")):
            groups = list(range(pq.ParquetFile(f).metadata.num_row_groups))
            chunks += [(f, groups[i:i + CHUNK_ROW_GROUPS]) for i in range(0, len(groups), CHUNK_ROW_GROUPS)]
        yield origin_dir.name, chunks


def _all_sources():
    return sorted({d.name for r in CANONICAL_ROOTS if r.is_dir() for d in r.iterdir() if d.is_dir()})


def run(sources, workers):
    sources = sorted(set(sources))  # a repeated name would stage the same (source, origin) twice
    jobs, pending, totals = [], {}, defaultdict(lambda: {"docs": 0, "kept": 0, "syllables_in": 0,
                                                         "syllables_out": 0, "reasons": Counter()})
    for source in sources:
        for origin, chunks in _inputs(source):
            keep_tmp = staging_dir(source, origin, PROCESSED_DIR / STAGE)
            reject_tmp = staging_dir(source, origin, PROCESSED_DIR / REJECTED)
            pending[(source, origin)] = [len(chunks), keep_tmp, reject_tmp]
            jobs += [(f, groups, source, origin, keep_tmp, reject_tmp, f"c{i:06d}")
                     for i, (f, groups) in enumerate(chunks)]
    with ProcessPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(process_chunk, job): job for job in jobs}
        for future in as_completed(futures):
            _, _, source, origin, *_ = futures[future]
            stats, total = future.result(), totals[source]
            for key in ("docs", "kept", "syllables_in", "syllables_out"):
                total[key] += stats[key]
            total["reasons"].update(stats["reasons"])
            entry = pending[(source, origin)]
            entry[0] -= 1
            if entry[0] == 0:
                publish(entry[1], source, origin, PROCESSED_DIR / STAGE)
                publish(entry[2], source, origin, PROCESSED_DIR / REJECTED)
                print(f"{source}/{origin}: done", flush=True)
    for source, total in totals.items():
        out = {"stage": STAGE, "config": config_hash(), **{k: v for k, v in total.items() if k != "reasons"},
               "rejected_by_reason": dict(sorted(total["reasons"].items()))}
        (PROCESSED_DIR / STAGE / source / "stats.json").write_text(json.dumps(out, indent=1), encoding="utf-8")
        print(f"{source}: kept {total['kept']}/{total['docs']} {dict(total['reasons'].most_common(6))}", flush=True)


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("sources", nargs="*")
    parser.add_argument("--workers", type=int, default=8, help="each worker holds GlotLID (~1.6 GB RAM)")
    args = parser.parse_args()
    run(args.sources or _all_sources(), args.workers)


if __name__ == "__main__":
    main()
