"""
Convert downloaded open pretraining datasets (manifests/datasets/*.yaml with
`purpose: pretrain` and a `fields` mapping) into canonical DocumentRecords.

    uv run python -m pipeline.ingest.open_dataset_export fineweb2_vie [--workers 6] [--limit-files N]

Publish date is unknown for web dumps; `crawl_time` (the capture time) still gives
era=pre_llm for captures before the cutoff. doc_id locator is "<repo>#<row id>"
(or file+row when the dataset has no id) because the same URL recurs across crawls.
Files are processed in parallel; each worker writes its own shards into a shared
staging dir that is published atomically at the end.
"""
import argparse
import json
import sys
import unicodedata
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime

import pyarrow.parquet as pq

from pipeline.acquire.hf_snapshot import load_declarations, target_dir
from pipeline.ingest.canonical_writer import ShardWriter, publish, staging_dir
from pipeline.paths import DATA_ROOT, REPORTS_DIR
from pipeline.schema.document_record import era_for, make_doc_id, text_sha256, validate

BATCH_ROWS = 2000


def _nfc(text):
    return text if unicodedata.is_normalized("NFC", text) else unicodedata.normalize("NFC", text)


def crawl_time_of(value):
    """Datasets disagree on format: FineWeb '2013-12-09T03:22:04Z', CulturaX '2018/09/23 04:49:28'."""
    if isinstance(value, datetime):
        return value.replace(tzinfo=None)
    text = str(value).strip().replace("Z", "+00:00")
    if text[4:5] == "/":
        text = text.replace("/", "-", 2)
    return datetime.fromisoformat(text).replace(tzinfo=None)


def convert_file(args):
    decl, path, file_index, tmp_dir = args
    fields, keep_if = decl["fields"], decl.get("keep_if") or {}
    fallback = decl.get("crawl_time_fallback") or {}
    columns = sorted({c for c in (fields.get("id"), fields["text"], fields.get("url"), fields.get("crawl_time"),
                                  fallback.get("column")) if c}
                     | set(fields.get("meta", [])) | set(keep_if))
    writer = ShardWriter(decl["name"], "hf", tmp_dir=tmp_dir, shard_prefix=f"f{file_index:05d}")
    stats = {"rows": 0, "written": 0, "filtered": 0, "empty": 0, "invalid": 0, "no_crawl_time": 0,
             "crawl_time_fallback": 0, "pre_llm": 0}
    rel = path.relative_to(DATA_ROOT).as_posix()
    row_no = 0
    for batch in pq.ParquetFile(path).iter_batches(batch_size=BATCH_ROWS, columns=columns):
        for row in batch.to_pylist():
            row_no += 1
            stats["rows"] += 1
            if any(row.get(k) != v for k, v in keep_if.items()):
                stats["filtered"] += 1
                continue
            text = _nfc((row[fields["text"]] or "").strip())
            if not text:
                stats["empty"] += 1
                continue
            meta = {k: row.get(k) for k in fields.get("meta", [])}
            raw_time = row.get(fields["crawl_time"]) if fields.get("crawl_time") else decl.get("default_crawl_time")
            if raw_time is None or not str(raw_time).strip():
                # Only a declared, documented bound may stand in for capture time; never guess.
                raw_time = fallback.get("values", {}).get(row.get(fallback.get("column")))
                if raw_time is None:
                    stats["no_crawl_time"] += 1
                    continue
                stats["crawl_time_fallback"] += 1
                meta["crawl_time_inferred"] = True
            elif not fields.get("crawl_time"):
                meta["crawl_time_inferred"] = True
            crawl_time = crawl_time_of(raw_time)
            row_id = row.get(fields["id"]) if fields.get("id") else f"{path.name}:{row_no}"
            era = era_for(None, crawl_time)
            record = {
                "doc_id": make_doc_id(decl["name"], f"{decl['repo']}#{row_id}"), "source_id": decl["name"],
                "source_type": "open_dataset", "url": row.get(fields.get("url") or ""), "title": None,
                "text": text, "lang": None, "lang_score": None, "publish_date": None,
                "publish_date_source": "none", "crawl_time": crawl_time, "era": era, "author": None,
                "category": None, "tags": [], "raw_ref": f"{rel}#row={row_no}", "text_sha256": text_sha256(text),
                "license": decl["license"], "usage_scope": decl["usage_scope"],
                "extractor": f"{decl['name']}@{decl['revision'][:12]}",
                "meta": json.dumps(meta, ensure_ascii=False, default=str),
            }
            if validate(record):
                stats["invalid"] += 1
                continue
            writer.write(record)
            stats["written"] += 1
            stats["pre_llm"] += era == "pre_llm"
    writer.close()
    return stats


def export(name, workers, limit_files):
    decl = load_declarations()[name]
    files = sorted(target_dir(decl).rglob("*.parquet"))[: limit_files or None]
    if not files:
        raise SystemExit(f"{name}: no parquet files under {target_dir(decl)}; download first")
    tmp = staging_dir(name, "hf")
    totals = {}
    try:
        with ProcessPoolExecutor(max_workers=workers) as pool:
            for i, stats in enumerate(pool.map(convert_file, [(decl, f, i, tmp) for i, f in enumerate(files)]), 1):
                for k, v in stats.items():
                    totals[k] = totals.get(k, 0) + v
                print(f"{name}: {i}/{len(files)} files {totals}", flush=True)
        publish(tmp, name, "hf")
    except BaseException:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    (REPORTS_DIR / "export").mkdir(parents=True, exist_ok=True)
    (REPORTS_DIR / "export" / f"{name}.json").write_text(json.dumps({"files": len(files), **totals}, indent=1), encoding="utf-8")


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("names", nargs="+")
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--limit-files", type=int, default=0, help="smoke test on the first N files")
    args = parser.parse_args()
    for name in args.names:
        export(name, args.workers, args.limit_files)


if __name__ == "__main__":
    main()
