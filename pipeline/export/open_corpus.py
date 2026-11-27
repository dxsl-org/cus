"""
Publish the openly licensed Vietnamese text that this pipeline collected and cleaned to DXSL/cus-vi-open.

  LLM_DATA_ROOT=E:/llm-data uv run --group process python -m pipeline.export.open_corpus [--dry-run]

Only sources this project produced are published; open datasets already on the Hub (FineWeb-2, CulturaX,
HPLT, FinePDFs, the hf_* mirrors) are linked from the card instead of re-uploaded. Per document:
- dropped unless its own `license` is in OPEN_LICENSES and its usage_scope is not blocked;
- dropped if it covers a frozen eval item (pipeline.process.decontam), so no holdout question leaks;
- PII-masked (pipeline.process.pii_vi) when read from canonical; s4_clean is already masked.
Output: data/<source_id>/<nnnnn>.parquet (doc_id, source_id, url, title, text, publish_date, license), one Hub
config per source, and a card with per-source counts pinned to the git commit. Token: see pipeline.hf_hub.
"""
import argparse
import pathlib
import tempfile
from collections import Counter

import pyarrow as pa
import pyarrow.parquet as pq

from pipeline.hf_hub import folder_size, git_commit, render_card, upload_dataset
from pipeline.paths import PROCESSED_DIR, canonical_source_dir
from pipeline.process.decontam import EvalIndex
from pipeline.process.pii_vi import mask

# toaan_gov_vn (court rulings) is left out: they name private parties, and personal-data rules apply even
# though rulings carry no copyright. kcb_vn is left out: its pages are hospital news, not the administrative
# texts the vn-not-protected label covers.
SOURCES = ("vbpl_vn", "vi_wikipedia_org", "vi_wikisource_org", "vi_wiktionary_org", "vi_wikibooks_org",
           "vi_wikiquote_org", "vi_wikivoyage_org", "vnoi_wiki")
OPEN_LICENSES = {"vn-not-protected", "cc-by-sa-4.0", "cc-by-4.0", "cc0-1.0", "odc-by-1.0", "apache-2.0", "agpl-3.0"}
COLUMNS = ["doc_id", "source_id", "url", "title", "text", "publish_date", "license"]
READ_COLUMNS = COLUMNS + ["source_type", "usage_scope"]
SHARD_BYTES = 256 * 2**20
CARD = pathlib.Path(__file__).parent / "open-corpus-card.md"


def source_files(source_id):
    """(parquet files, already masked): s4_clean when the source went through phase 6, else canonical."""
    clean = PROCESSED_DIR / "s4_clean" / source_id
    if clean.is_dir():
        return sorted(clean.rglob("*.parquet")), True
    root = canonical_source_dir(source_id)
    if root is None:
        raise SystemExit(f"no s4_clean or canonical data for {source_id}")
    return sorted(root.rglob("*.parquet")), False


def export_source(source_id, index, out):
    files, masked = source_files(source_id)
    stats, rows, size, shard = Counter(), [], 0, 0
    dest = out / "data" / source_id
    dest.mkdir(parents=True)

    def flush():
        nonlocal rows, size, shard
        if rows:
            pq.write_table(pa.Table.from_pylist(rows), dest / f"{shard:05d}.parquet", compression="zstd")
            rows, size, shard = [], 0, shard + 1

    for path in files:
        for doc in pq.read_table(path, columns=READ_COLUMNS).to_pylist():
            if not doc["text"] or doc["usage_scope"] == "blocked" or doc["license"] not in OPEN_LICENSES:
                stats["dropped_license"] += 1
                continue
            if index.matches(doc["text"]):
                stats["dropped_decontam"] += 1
                continue
            if not masked:
                doc["text"] = mask(doc["text"], doc["source_type"])[0]
            rows.append({c: doc[c] for c in COLUMNS})
            stats["docs"] += 1
            stats["bytes"] += len(doc["text"].encode("utf-8"))
            size += len(doc["text"])
            if size >= SHARD_BYTES:
                flush()
    flush()
    return stats


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--repo", default="DXSL/cus-vi-open")
    parser.add_argument("--dry-run", action="store_true", help="stage into a temp dir and print the plan only")
    args = parser.parse_args()
    index = EvalIndex()
    with tempfile.TemporaryDirectory(dir=PROCESSED_DIR) as tmp:
        out = pathlib.Path(tmp)
        stats = {}
        for source_id in SOURCES:
            stats[source_id] = export_source(source_id, index, out)
            print(source_id, dict(stats[source_id]), flush=True)
        commit = git_commit()
        table = "\n".join(f"| `{s}` | {st['docs']:,} | {st['bytes'] / 1e6:,.0f} | {st['dropped_decontam']} |"
                          for s, st in stats.items())
        configs = "\n".join(f"- config_name: {s}\n  data_files: data/{s}/*.parquet" for s in SOURCES)
        render_card(CARD, out, commit=commit, table=table, configs=configs, evalsets=", ".join(index.evalsets))
        print(f"{folder_size(out) / 1e9:.2f} GB")
        if not args.dry_run:
            print(upload_dataset(args.repo, out, ["data/**"], f"Sync open corpus from dxsl-org/cus@{commit[:7]}"))


if __name__ == "__main__":
    main()
