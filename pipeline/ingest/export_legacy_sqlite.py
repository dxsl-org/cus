"""
Export legacy Craw-Web-Data SQLite DBs to canonical Parquet (read-only on the DBs).

    uv run python -m pipeline.ingest.export_legacy_sqlite                 # all sources
    uv run python -m pipeline.ingest.export_legacy_sqlite plo_vn toaan_gov_vn

Per source: DATA_ROOT/canonical/<source_id>/part-*.parquet (atomically replaced),
DATA_ROOT/reports/export/<source_id>.json (counts) and <source_id>.rejects.jsonl
(records failing schema validation). Publish-date patches are merged in.
"""
import argparse
import collections
import json
import sqlite3
import sys
from concurrent.futures import ProcessPoolExecutor

from pipeline.ingest import patch_store
from pipeline.ingest.canonical_writer import ShardWriter
from pipeline.ingest.legacy_record import build_record
from pipeline.paths import LEGACY_DATASET_DIR, REPORTS_DIR
from pipeline.schema import sources_manifest
from pipeline.schema.document_record import validate

FETCH_ROWS = 1000


def _rows(db_path):
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        cursor = conn.execute("SELECT * FROM articles ORDER BY id")
        while batch := cursor.fetchmany(FETCH_ROWS):
            yield from (dict(r) for r in batch)
    finally:
        conn.close()


def export_source(source):
    source_id = source["id"]
    report_dir = REPORTS_DIR / "export"
    report_dir.mkdir(parents=True, exist_ok=True)
    stats = collections.Counter()
    date_tags, eras = collections.Counter(), collections.Counter()
    date_patches = patch_store.PUBLISH_DATES.load(source_id)
    seen = set()
    writer = ShardWriter(source_id, "legacy")
    try:
        with open(report_dir / f"{source_id}.rejects.jsonl", "w", encoding="utf-8") as rejects:
            for db_name in source["legacy"]["dbs"]:
                for row in _rows(LEGACY_DATASET_DIR / db_name):
                    stats["rows"] += 1
                    record, skip = build_record(source, row, db_name, date_patches)
                    if skip:
                        stats[skip] += 1
                        continue
                    if record["doc_id"] in seen:
                        stats["duplicate_doc_id"] += 1
                        continue
                    errors = validate(record)
                    if errors:
                        stats["invalid"] += 1
                        rejects.write(json.dumps({"legacy_db": db_name, "legacy_id": row.get("id"),
                                                  "errors": errors}, ensure_ascii=False) + "\n")
                        continue
                    seen.add(record["doc_id"])
                    writer.write(record)
                    stats["written"] += 1
                    stats["text_chars"] += len(record["text"])
                    stats["broken_diacritics"] += '"text_issue": "broken_diacritics"' in record["meta"]
                    date_tags[record["publish_date_source"].split(":")[0]] += 1
                    eras[record["era"]] += 1
        writer.commit()
    except BaseException:
        writer.abort()
        raise
    report = {"source_id": source_id, **stats, "shards": writer.shards,
              "date_source": dict(date_tags), "era": dict(eras),
              "date_patches": len(date_patches)}
    (report_dir / f"{source_id}.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    return report


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("sources", nargs="*", help="source ids (default: every source with legacy DBs)")
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args()
    # legacy.export: false = source is ingested by a dedicated importer (e.g. Wikipedia dumps).
    entries = [e for e in sources_manifest.load()
               if e.get("legacy", {}).get("dbs") and e["legacy"].get("export", True)]
    if args.sources:
        unknown = set(args.sources) - {e["id"] for e in entries}
        if unknown:
            parser.error(f"unknown or DB-less sources: {sorted(unknown)}")
        entries = [e for e in entries if e["id"] in args.sources]
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        for r in pool.map(export_source, entries):
            dated = r.get("written", 0) - r["date_source"].get("none", 0)
            print(f"{r['source_id']:26} rows={r.get('rows', 0):>7} written={r.get('written', 0):>7} "
                  f"empty={r.get('empty_content', 0):>5} dup={r.get('duplicate_doc_id', 0):>5} "
                  f"invalid={r.get('invalid', 0):>4} dated={dated} broken={r.get('broken_diacritics', 0)}", flush=True)


if __name__ == "__main__":
    main()
