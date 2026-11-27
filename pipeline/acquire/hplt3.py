"""
Acquire and export HPLT 3.0 sorted Vietnamese tiers 9-10 (`vie_Latn`) to canonical DocumentRecords.

    uv run python -m pipeline.acquire.hplt3 download [--files 10_1.jsonl.zst ...]
    uv run python -m pipeline.acquire.hplt3 export [--files 10_1.jsonl.zst ...]

Streams .jsonl.zst via pyarrow.CompressedInputStream(..., "zstd") without extra pip packages.
"""
import argparse
import hashlib
import io
import json
import subprocess
import sys
import unicodedata
from datetime import datetime

import pyarrow as pa

from pipeline.ingest.canonical_writer import ShardWriter
from pipeline.ingest.open_dataset_export import crawl_time_of
from pipeline.paths import DATA_ROOT, RAW_DIR, REPORTS_DIR
from pipeline.schema.document_record import era_for, make_doc_id, text_sha256, validate

SOURCE_ID = "hplt3_vie_hq"
BASE_URL = "https://data.hplt-project.org/three/sorted/vie_Latn/"
FILES = (
    "10_1.jsonl.zst",
    "9_1.jsonl.zst",
    "9_2.jsonl.zst",
)


def _nfc(text):
    return text if unicodedata.is_normalized("NFC", text) else unicodedata.normalize("NFC", text)


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(16 * 2**20):
            h.update(chunk)
    return h.hexdigest()


def parse_hplt3_row(row, path_name, row_no, rel_path):
    """Convert one parsed HPLT 3.0 JSON object to a canonical DocumentRecord or None."""
    if row.get("filter") not in (None, "keep"):
        return None
    text = _nfc((row.get("text") or "").strip())
    if not text:
        return None
    raw_ts = row.get("ts")
    if not raw_ts or not str(raw_ts).strip():
        return None
    crawl_time = crawl_time_of(raw_ts)
    era = era_for(None, crawl_time)
    row_id = row.get("id") or f"{path_name}:{row_no}"
    tier = path_name.split("_", 1)[0]
    meta = {
        "crawl_id": row.get("crawl_id"),
        "tier": tier,
        "cluster_size": row.get("cluster_size"),
        "doc_scores": row.get("doc_scores"),
        "web_register": row.get("web-register"),
    }
    return {
        "doc_id": make_doc_id(SOURCE_ID, f"HPLT3.0#{row_id}"),
        "source_id": SOURCE_ID,
        "source_type": "open_dataset",
        "url": row.get("u"),
        "title": None,
        "text": text,
        "lang": None,
        "lang_score": None,
        "publish_date": None,
        "publish_date_source": "none",
        "crawl_time": crawl_time,
        "era": era,
        "author": None,
        "category": f"tier_{tier}",
        "tags": [],
        "raw_ref": f"{rel_path}#row={row_no}",
        "text_sha256": text_sha256(text),
        "license": "cc0-1.0",
        "usage_scope": "train_ok",
        "extractor": f"hplt3_vie_hq@3.0-t{tier}",
        "meta": json.dumps(meta, ensure_ascii=False, default=str),
    }


def download(selected_files):
    raw_dir = RAW_DIR / "open_datasets" / SOURCE_ID
    raw_dir.mkdir(parents=True, exist_ok=True)
    lock_path = raw_dir / "lock.json"
    lock = json.loads(lock_path.read_text(encoding="utf-8")) if lock_path.exists() else {"files": {}}
    for fname in selected_files:
        dest = raw_dir / fname
        if not dest.exists():
            part = raw_dir / f"{fname}.part"
            url = BASE_URL + fname
            print(f"Downloading {url} -> {dest}", flush=True)
            subprocess.run(
                ["curl", "-4", "-fsSL", "--retry", "20", "--retry-all-errors", "--retry-delay", "15",
                 "-C", "-", "-o", str(part), url],
                check=True,
            )
            part.rename(dest)
        sha = _sha256(dest)
        lock["files"][fname] = {
            "url": BASE_URL + fname,
            "size": dest.stat().st_size,
            "sha256": sha,
        }
        lock_path.write_text(json.dumps(lock, indent=1), encoding="utf-8")
        print(f"Verified {fname}: {dest.stat().st_size / 1048576:.1f} MB sha256={sha[:12]}", flush=True)


def export(selected_files):
    raw_dir = RAW_DIR / "open_datasets" / SOURCE_ID
    paths = [raw_dir / f for f in selected_files if (raw_dir / f).exists()]
    if not paths:
        raise FileNotFoundError(f"No matching .jsonl.zst files under {raw_dir}")
    writer = ShardWriter(SOURCE_ID, "hf")
    stats = {"files": [], "rows": 0, "written": 0, "skipped": 0, "invalid": 0, "era": {}}
    try:
        for path in paths:
            rel = path.relative_to(DATA_ROOT).as_posix()
            f_written = 0
            with pa.CompressedInputStream(pa.OSFile(str(path)), "zstd") as cs:
                reader = io.TextIOWrapper(io.BufferedReader(cs, buffer_size=4 * 1024 * 1024), encoding="utf-8", errors="replace")
                for row_no, line in enumerate(reader, 1):
                    if not line.strip():
                        continue
                    stats["rows"] += 1
                    rec = parse_hplt3_row(json.loads(line), path.name, row_no, rel)
                    if rec is None:
                        stats["skipped"] += 1
                    elif validate(rec):
                        stats["invalid"] += 1
                    else:
                        writer.write(rec)
                        stats["written"] += 1
                        f_written += 1
                        stats["era"][rec["era"]] = stats["era"].get(rec["era"], 0) + 1
                    if stats["rows"] % 100000 == 0:
                        print(f"hplt3: {path.name} rows={stats['rows']} written={stats['written']}", flush=True)
            stats["files"].append({"name": path.name, "written": f_written})
            print(f"hplt3 finished {path.name}: written={f_written}", flush=True)
        writer.commit()
    except BaseException:
        writer.abort()
        raise
    (REPORTS_DIR / "export").mkdir(parents=True, exist_ok=True)
    (REPORTS_DIR / "export" / f"{SOURCE_ID}.json").write_text(json.dumps(stats, indent=1, ensure_ascii=False), encoding="utf-8")
    return stats


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["download", "export"])
    parser.add_argument("--files", nargs="*", default=list(FILES))
    args = parser.parse_args()
    unknown = set(args.files) - set(FILES)
    if unknown:
        parser.error(f"unknown HPLT3 files: {sorted(unknown)}")
    if args.command == "download":
        download(args.files)
    else:
        print(json.dumps(export(args.files), ensure_ascii=False))


if __name__ == "__main__":
    main()
