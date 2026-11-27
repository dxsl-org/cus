"""
Phase 7 steps 2-3: PDFs with a text layer (Unicode, or TCVN3/VNI converted page by page) ->
DocumentRecords (one per book), and a status for every inventoried file. Run pipeline.books.inventory first.

    uv run python -m pipeline.books.extract_pdf [--workers 6]

Output: DATA_ROOT/canonical/owner_books/book/ (replaced atomically). books.sqlite gets `status`:
  extracted | ocr_pending (scan, or a text PDF whose pages are mostly images) | duplicate |
  failed:<reason> (unreadable, garbled text layer, a legacy font that does not convert) |
  image_unreviewed | skipped:<ext>. Ebook, office and text files (OTHER_KINDS) are left to
  pipeline.books.extract_ebook, which sets their status itself.
Dates: a book's publish date is unknown (`none`); crawl_time is when the file came to exist (the PDF's
own creation date when earlier than the file time), so a PDF made before the LLM cutoff is pre_llm.
"""
import argparse
import json
import re
import sqlite3
import sys
import unicodedata
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import pymupdf

from pipeline.books.inventory import DB_PATH, TEXT_PAGE_CHARS
from pipeline.books.legacy_vn_fonts import to_unicode
from pipeline.books.postprocess import clean
from pipeline.books.syllables import vi_score
from pipeline.ingest.canonical_writer import ShardWriter
from pipeline.schema.document_record import era_for, make_doc_id, text_sha256, validate

SOURCE_ID, ORIGIN = "owner_books", "book"
EXTRACTOR = f"pymupdf-{pymupdf.VersionBind}+books/v1"
LEGACY_ENCODINGS = ("tcvn3", "vni")
MAX_IMAGE_SHARE = 0.5  # more image-only pages than this: the book goes to OCR instead
# A converted book must read as Vietnamese afterwards; below this the font was some other private map.
MIN_CONVERTED_KNOWN = 0.85
COLUMNS = (("status", "TEXT"), ("doc_id", "TEXT"), ("chars", "INTEGER"), ("image_pages", "INTEGER"),
           ("vi_known", "REAL"), ("extracted_at", "TEXT"))
OTHER_KINDS = ("ebook", "office", "plain")
_PDF_DATE = re.compile(r"D:(\d{4})(\d{2})(\d{2})")


def created_time(meta, mtime):
    file_time = datetime.fromtimestamp(mtime, timezone.utc).replace(tzinfo=None)
    m = _PDF_DATE.match(meta.get("creationDate") or "")
    try:
        made = datetime(int(m[1]), int(m[2]), int(m[3])) if m else None
    except ValueError:
        made = None
    return min(file_time, made) if made and made.year >= 1990 else file_time


def clean_field(value):
    return unicodedata.normalize("NFC", " ".join((value or "").split())) or None


def extract(args):
    root, row = args
    path = Path(root) / row["path"]
    with pymupdf.open(path) as doc:
        pages = [p.get_text() for p in doc]
        meta = doc.metadata or {}
    image_pages = sum(len(p.strip()) < TEXT_PAGE_CHARS for p in pages)
    if not pages or image_pages / len(pages) > MAX_IMAGE_SHARE:
        return {"path": row["path"], "status": "ocr_pending", "image_pages": image_pages}
    if row["encoding"] in LEGACY_ENCODINGS:  # convert page by page: front matter is often already Unicode
        pages = [to_unicode(p)[0] for p in pages]
    text = clean(pages)
    known, vi_letters = vi_score(text)
    if row["encoding"] in LEGACY_ENCODINGS and (known or 0) < MIN_CONVERTED_KNOWN:
        return {"path": row["path"], "status": f"failed:unknown_font_map ({row['encoding']}?)", "vi_known": known}
    crawl_time = created_time(meta, row["mtime"])
    title = clean_field(meta.get("title")) or unicodedata.normalize("NFC", path.stem)
    record = {
        "doc_id": make_doc_id(SOURCE_ID, f"sha256:{row['sha256']}"), "source_id": SOURCE_ID, "source_type": "book",
        "url": None, "title": title, "text": text, "lang": None, "lang_score": None, "publish_date": None,
        "publish_date_source": "none", "crawl_time": crawl_time, "era": era_for(None, crawl_time),
        "author": clean_field(meta.get("author")),
        "category": row["path"].split("/")[0] if "/" in row["path"] else None, "tags": [],
        "raw_ref": f"file:{row['path']}", "text_sha256": text_sha256(text), "license": "copyrighted",
        "usage_scope": "train_ok", "extractor": EXTRACTOR,
        "meta": json.dumps({"pages": len(pages), "image_pages": image_pages, "encoding": row["encoding"],
                            "vi_known": round(known, 3) if known is not None else None,
                            "vi_letters": round(vi_letters, 3), "sha256": row["sha256"]}, ensure_ascii=False)}
    errors = validate(record)
    if errors:
        return {"path": row["path"], "status": "failed:" + "; ".join(errors)[:200]}
    return {"path": row["path"], "status": "extracted", "record": record, "image_pages": image_pages,
            "vi_known": known, "chars": len(text)}


def _status_without_extraction(r):
    if r["duplicate_of"]:
        return "duplicate"
    if r["error"]:
        return "failed:" + r["error"][:200]
    if r["kind"] == "pdf":
        if r["pdf_kind"] in ("pdf_scan", "pdf_empty"):
            return "ocr_pending"
        return {"garbled": "failed:garbled_text_layer"}.get(r["encoding"])  # None: decided by extract()
    return {"image": "image_unreviewed"}.get(r["kind"], f"skipped:{r['ext']}")


def run(root, workers):
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    for column, decl in COLUMNS:
        if column not in {r[1] for r in con.execute("PRAGMA table_info(files)")}:
            con.execute(f"ALTER TABLE files ADD COLUMN {column} {decl}")
    rows = [dict(r) for r in con.execute(f"SELECT * FROM files WHERE kind NOT IN {OTHER_KINDS} ORDER BY path")]
    todo = []
    for r in rows:
        status = _status_without_extraction(r)
        con.execute("UPDATE files SET status = ?, doc_id = NULL WHERE path = ?", (status, r["path"]))
        if status is None:
            todo.append(r)
    print(f"{len(todo)} text PDFs to extract", flush=True)
    writer, done = ShardWriter(SOURCE_ID, ORIGIN), Counter()
    try:
        with ProcessPoolExecutor(workers) as pool:
            for n, out in enumerate(pool.map(extract, [(root, r) for r in todo]), start=1):
                record = out.pop("record", None)
                if record:
                    writer.write(record)
                con.execute("UPDATE files SET status = ?, doc_id = ?, chars = ?, image_pages = ?, vi_known = ?, "
                            "extracted_at = ? WHERE path = ?",
                            (out["status"], record and record["doc_id"], out.get("chars"), out.get("image_pages"),
                             out.get("vi_known"), datetime.now(timezone.utc).isoformat(timespec="seconds"), out["path"]))
                done[out["status"].split(":")[0]] += 1
                if n % 50 == 0:
                    print(f"{n}/{len(todo)} {dict(done)}", flush=True)
        writer.commit()
    except BaseException:
        writer.abort()
        raise
    con.commit()
    return Counter(r[0].split(":")[0] for r in con.execute("SELECT status FROM files WHERE status IS NOT NULL"))


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default="D:/Book")
    parser.add_argument("--workers", type=int, default=6)
    args = parser.parse_args()
    print(json.dumps(run(args.root, args.workers), ensure_ascii=False))


if __name__ == "__main__":
    main()
