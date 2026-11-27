"""
Phase 7 step 1: inventory of a book folder (read-only on the books).

    uv run python -m pipeline.books.inventory D:/Book [--workers 6]

Every file gets a row in DATA_ROOT/books/books.sqlite: sha256, size, kind, and for PDFs the page
count, how many sampled pages carry a text layer and which Vietnamese encoding that text is in
(Unicode, or the legacy TCVN3 / VNI fonts that need conversion). Files whose path, size and mtime
are unchanged are not re-read, so re-running after adding books only scans the new ones.
Same sha256 = duplicate (first path in sort order is kept). Images are not classified one by one:
the report lists image folders, since only a person can tell a scanned book from a photo album.
Path prefixes listed in DATA_ROOT/books/ignore.txt are left out (and dropped from the DB).
Report: DATA_ROOT/reports/books/inventory.md.
"""
import argparse
import hashlib
import sqlite3
import sys
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import pymupdf

from pipeline.books.encoding import classify
from pipeline.paths import DATA_ROOT, REPORTS_DIR

DB_PATH = DATA_ROOT / "books" / "books.sqlite"
IGNORE_PATH = DATA_ROOT / "books" / "ignore.txt"  # owner-excluded path prefixes, e.g. a table-of-contents scan
KINDS = {"pdf": "pdf", "epub": "ebook", "mobi": "ebook", "prc": "ebook", "azw3": "ebook", "azw": "ebook",
         "fb2": "ebook", "djvu": "djvu", "doc": "office", "docx": "office", "odt": "office", "rtf": "office",
         "txt": "plain", "jpg": "image", "jpeg": "image", "png": "image", "tif": "image", "tiff": "image",
         "webp": "image", "bmp": "image", "rar": "archive", "zip": "archive", "7z": "archive"}
SAMPLE_PAGES = 8
TEXT_PAGE_CHARS = 200  # a page with fewer extracted characters counts as image-only
SCHEMA = """
CREATE TABLE IF NOT EXISTS files (
    path TEXT PRIMARY KEY, sha256 TEXT NOT NULL, ext TEXT NOT NULL, size INTEGER NOT NULL, mtime REAL NOT NULL,
    kind TEXT NOT NULL, pages INTEGER, sampled_pages INTEGER, text_pages INTEGER, sample_chars INTEGER,
    encoding TEXT, pdf_kind TEXT, error TEXT, duplicate_of TEXT, scanned_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS files_sha ON files (sha256);
"""


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while chunk := fh.read(1 << 22):
            h.update(chunk)
    return h.hexdigest()


def _pdf(path):
    """Pages, text layer on evenly spread sample pages, encoding of that text."""
    with pymupdf.open(path) as doc:
        if doc.needs_pass:
            return {"error": "encrypted"}
        n = doc.page_count
        picks = sorted({int(i * n / SAMPLE_PAGES) for i in range(SAMPLE_PAGES)}) if n else []
        texts = [doc[i].get_text() for i in picks]
    text_pages = sum(len(t.strip()) >= TEXT_PAGE_CHARS for t in texts)
    sample = "\n".join(texts)
    kind = ("pdf_scan" if text_pages == 0 else "pdf_text" if text_pages == len(picks) else "pdf_mixed") if n else "pdf_empty"
    return {"pages": n, "sampled_pages": len(picks), "text_pages": text_pages, "sample_chars": len(sample.strip()),
            "encoding": classify(sample) if text_pages else None, "pdf_kind": kind}


def scan_file(args):
    root, rel = args
    path = Path(root) / rel
    ext = path.suffix.lower().lstrip(".")
    stat = path.stat()
    row = {"path": rel, "ext": ext, "size": stat.st_size, "mtime": stat.st_mtime, "kind": KINDS.get(ext, "other"),
           "scanned_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    try:
        row["sha256"] = _sha256(path)
        if row["kind"] == "pdf":
            row.update(_pdf(path))
    except Exception as e:  # a corrupt file is a finding, not a reason to stop the inventory
        row.setdefault("sha256", "")
        row["error"] = f"{type(e).__name__}: {e}"[:300]
    return row


def _ignored():
    """Path prefixes (relative to the book root, one per line) the owner excluded from tracking."""
    return [l.strip().replace("\\", "/") for l in IGNORE_PATH.read_text(encoding="utf-8").splitlines()
            if l.strip() and not l.startswith("#")] if IGNORE_PATH.is_file() else []


def run(root, workers):
    root = Path(root)
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    con.executescript(SCHEMA)
    known = {r[0]: (r[1], r[2]) for r in con.execute("SELECT path, size, mtime FROM files")}
    ignored = tuple(_ignored())
    rels = sorted(rel for rel in (p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file())
                  if not rel.startswith(ignored))
    todo = [r for r in rels if known.get(r) != ((s := (root / r).stat()).st_size, s.st_mtime)]
    con.executemany("DELETE FROM files WHERE path = ?", [(p,) for p in set(known) - set(rels)])
    print(f"{len(rels)} files, {len(todo)} to scan", flush=True)
    cols = ("path", "sha256", "ext", "size", "mtime", "kind", "pages", "sampled_pages", "text_pages",
            "sample_chars", "encoding", "pdf_kind", "error", "scanned_at")
    with ProcessPoolExecutor(workers) as pool:
        for n, row in enumerate(pool.map(scan_file, [(str(root), r) for r in todo], chunksize=4), start=1):
            con.execute(f"INSERT OR REPLACE INTO files ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                        [row.get(c) for c in cols])
            if n % 500 == 0:
                con.commit()
                print(f"{n}/{len(todo)}", flush=True)
    con.execute("UPDATE files SET duplicate_of = (SELECT min(f.path) FROM files f WHERE f.sha256 = files.sha256 "
                "AND f.path < files.path) WHERE sha256 <> ''")
    con.commit()
    return con


def report(con, root):
    from pipeline.books.inventory_report import render
    out = REPORTS_DIR / "books" / "inventory.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render(con, root), encoding="utf-8")
    return out


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("root")
    parser.add_argument("--workers", type=int, default=6)
    args = parser.parse_args()
    con = run(args.root, args.workers)
    print(report(con, args.root))
    print(Counter(r[0] for r in con.execute("SELECT kind FROM files WHERE duplicate_of IS NULL")))


if __name__ == "__main__":
    main()
