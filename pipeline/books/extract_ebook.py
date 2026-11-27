"""
Phase 7: book files that are not PDFs (EPUB, FB2, MOBI/PRC/AZW/AZW3, DOCX, DOC, RTF, ODT, TXT) ->
DocumentRecords, one per book. Run pipeline.books.inventory first; independent of extract_pdf.

    uv run python -m pipeline.books.extract_ebook [--workers 4]

Output: DATA_ROOT/canonical/owner_books/ebook/ (replaced atomically). books.sqlite `status` for these
kinds: extracted | duplicate | failed:drm (encrypted Kindle book, never decrypted) |
failed:<reason> (unreadable, too short, a legacy font that does not convert).
Text in TCVN3/VNI fonts (old .doc and .txt files) is converted to Unicode. The folders above the file
become `tags` (e.g. ["Tieu thuyet", "2-1945-1975"]), so the mix can weight novels by era and origin.
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

from pipeline.books.ebook_readers import DrmError, UnreadableError, read_paragraphs
from pipeline.books.encoding import classify
from pipeline.books.extract_pdf import (COLUMNS, LEGACY_ENCODINGS, MIN_CONVERTED_KNOWN, OTHER_KINDS, SOURCE_ID,
                                        clean_field, created_time)
from pipeline.books.inventory import DB_PATH
from pipeline.books.legacy_vn_fonts import to_unicode
from pipeline.books.syllables import vi_score
from pipeline.ingest.canonical_writer import ShardWriter
from pipeline.schema.document_record import era_for, make_doc_id, text_sha256, validate

ORIGIN = "ebook"
EXTRACTOR = "books-ebook/v1"
MIN_CHARS = 2000  # shorter is a cover note, a link list or a stub, not a book
CONVERT_CHUNK = 40  # paragraphs per font-detection chunk: front matter is often Unicode, the body not
_PAGE_NUMBER = re.compile(r"^\d{1,4}$")


def _text(paragraphs):
    """(text, encoding). Legacy fonts are converted chunk by chunk, like PDF pages."""
    encoding = classify("\n".join(paragraphs[:2000]))
    if encoding in LEGACY_ENCODINGS:
        chunks = ["\n".join(paragraphs[i:i + CONVERT_CHUNK]) for i in range(0, len(paragraphs), CONVERT_CHUNK)]
        paragraphs = "\n".join(to_unicode(c)[0] for c in chunks).split("\n")
    lines = (" ".join(unicodedata.normalize("NFC", p).split()) for p in paragraphs)
    return "\n".join(l for l in lines if l and not _PAGE_NUMBER.match(l)), encoding


def extract(args):
    root, row = args
    path = Path(root) / row["path"]
    try:
        text, encoding = _text(read_paragraphs(path))
    except DrmError:
        return {"path": row["path"], "status": "failed:drm"}
    except Exception as e:  # third-party parsers (MuPDF, KindleUnpack) fail on bad files in their own ways
        return {"path": row["path"], "status": f"failed:unreadable ({type(e).__name__}: {e})"[:200]}
    if len(text) < MIN_CHARS:
        return {"path": row["path"], "status": "failed:too_short", "chars": len(text)}
    known, vi_letters = vi_score(text)
    if encoding in LEGACY_ENCODINGS and (known or 0) < MIN_CONVERTED_KNOWN:
        return {"path": row["path"], "status": f"failed:unknown_font_map ({encoding}?)", "vi_known": known}
    folders = row["path"].split("/")[:-1]
    crawl_time = created_time({}, row["mtime"])
    record = {
        "doc_id": make_doc_id(SOURCE_ID, f"sha256:{row['sha256']}"), "source_id": SOURCE_ID, "source_type": "book",
        "url": None, "title": clean_field(path.stem), "text": text, "lang": None, "lang_score": None,
        "publish_date": None, "publish_date_source": "none", "crawl_time": crawl_time,
        "era": era_for(None, crawl_time), "author": None, "category": folders[0] if folders else None,
        "tags": [clean_field(f) for f in folders], "raw_ref": f"file:{row['path']}", "text_sha256": text_sha256(text),
        "license": "copyrighted", "usage_scope": "train_ok", "extractor": EXTRACTOR,
        "meta": json.dumps({"format": row["ext"], "encoding": encoding,
                            "vi_known": round(known, 3) if known is not None else None,
                            "vi_letters": round(vi_letters, 3), "sha256": row["sha256"]}, ensure_ascii=False)}
    errors = validate(record)
    if errors:
        return {"path": row["path"], "status": "failed:" + "; ".join(errors)[:200]}
    return {"path": row["path"], "status": "extracted", "record": record, "vi_known": known, "chars": len(text)}


def run(root, workers):
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    for column, decl in COLUMNS:
        if column not in {r[1] for r in con.execute("PRAGMA table_info(files)")}:
            con.execute(f"ALTER TABLE files ADD COLUMN {column} {decl}")
    rows = [dict(r) for r in con.execute(f"SELECT * FROM files WHERE kind IN {OTHER_KINDS} ORDER BY path")]
    todo = []
    for r in rows:
        status = "duplicate" if r["duplicate_of"] else "failed:" + r["error"][:200] if r["error"] else None
        con.execute("UPDATE files SET status = ?, doc_id = NULL WHERE path = ?", (status, r["path"]))
        if status is None:
            todo.append(r)
    print(f"{len(todo)} ebook/office/text files to extract", flush=True)
    writer, done = ShardWriter(SOURCE_ID, ORIGIN), Counter()
    try:
        with ProcessPoolExecutor(workers) as pool:
            for n, out in enumerate(pool.map(extract, [(root, r) for r in todo]), start=1):
                record = out.pop("record", None)
                if record:
                    writer.write(record)
                con.execute("UPDATE files SET status = ?, doc_id = ?, chars = ?, vi_known = ?, extracted_at = ? "
                            "WHERE path = ?",
                            (out["status"], record and record["doc_id"], out.get("chars"), out.get("vi_known"),
                             datetime.now(timezone.utc).isoformat(timespec="seconds"), out["path"]))
                done[out["status"].split(" ")[0]] += 1
                if n % 50 == 0:
                    print(f"{n}/{len(todo)} {dict(done)}", flush=True)
        writer.commit()
    except BaseException:
        writer.abort()
        raise
    con.commit()
    return dict(done)


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default="D:/Book")
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args()
    print(json.dumps(run(args.root, args.workers), ensure_ascii=False))


if __name__ == "__main__":
    main()
