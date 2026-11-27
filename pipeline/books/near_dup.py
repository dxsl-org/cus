"""
Phase 7: the same book stored twice with different bytes (EPUB and PRC of one title, two editions of
one ebook, a PDF and its EPUB). Exact copies are already caught by sha256 in the inventory; this
compares extracted text.

    uv run python -m pipeline.books.near_dup [--delete]

Each book -> a sample of its 5-word shingles (hash mod SAMPLE == 0: the same shingles are kept in
every book, so samples stay comparable). Two books are copies when their samples overlap with
Jaccard >= MIN_JACCARD; within a group of copies the best file is kept (format rank, then readable
Vietnamese share, then length). One book that only contains another (a single volume inside a
collected edition) is listed, never deleted. Report: DATA_ROOT/reports/books/near-dup.md (dry run) or
near-dup-deleted-<time>.md (--delete: the record of what went to the Recycle Bin).
--delete sends the other copies to the Recycle Bin; run the inventory and both extractors afterwards.
Scans (no text yet) are not compared.
"""
import argparse
import json
import re
import sqlite3
import sys
import zlib
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

import numpy as np
import pyarrow.parquet as pq

from pipeline.books.inventory import DB_PATH
from pipeline.books.recycle import recycle
from pipeline.paths import DATA_ROOT

SHINGLE = 5
SAMPLE = 32
MAX_DF = 20  # a shingle in more books than this is boilerplate ("Ebook thực hiện dành cho …"), not evidence
MIN_JACCARD = 0.7
MIN_CONTAINED = 0.8  # share of the smaller book's sample found in the bigger one
MIN_SAMPLE = 30  # fewer sampled shingles than this: too short to judge
FORMAT_RANK = {"epub": 0, "azw3": 1, "fb2": 1, "mobi": 2, "azw": 2, "prc": 2, "docx": 3, "odt": 3, "doc": 4,
               "rtf": 4, "txt": 5, "pdf": 6}
_WORD = re.compile(r"\w+")
_MULT = np.array([pow(1000003, k, 2**64) for k in range(SHINGLE)], dtype=np.uint64)


def shingle_sample(text):
    """Sorted unique sampled shingle hashes. crc32, not Python's per-process str hash: a dry run and the
    --delete run must sample the same shingles, or borderline pairs flip between them."""
    words = np.fromiter((zlib.crc32(w.encode()) for w in _WORD.findall(text.lower())), dtype=np.uint64)
    if len(words) < SHINGLE:
        return np.empty(0, np.uint64)
    windows = np.lib.stride_tricks.sliding_window_view(words, SHINGLE)
    with np.errstate(over="ignore"):
        hashes = (windows * _MULT).sum(axis=1, dtype=np.uint64)
    return np.unique(hashes[hashes % SAMPLE == 0])


def _books():
    meta = {r[0]: r[1:] for r in sqlite3.connect(DB_PATH).execute("SELECT path, ext, vi_known, chars FROM files")}
    for part in sorted((DATA_ROOT / "canonical" / "owner_books").glob("[!.]*/*.parquet")):
        for batch in pq.ParquetFile(part).iter_batches(columns=["raw_ref", "text"], batch_size=16):
            for ref, text in zip(batch.column("raw_ref").to_pylist(), batch.column("text").to_pylist()):
                path = ref.removeprefix("file:")
                if path in meta:
                    yield path, meta[path], shingle_sample(text)


def pairs(samples):
    """{(i, j): shared sampled shingles} for books sharing at least one non-boilerplate shingle."""
    doc = np.concatenate([np.full(len(s), i, np.int32) for i, s in enumerate(samples)])
    hashes = np.concatenate(samples)
    order = np.argsort(hashes, kind="stable")
    hashes, doc = hashes[order], doc[order]
    starts = np.flatnonzero(np.r_[True, hashes[1:] != hashes[:-1]])
    sizes = np.diff(np.r_[starts, len(hashes)])
    shared = Counter()
    for start, size in zip(starts[(sizes > 1) & (sizes <= MAX_DF)], sizes[(sizes > 1) & (sizes <= MAX_DF)]):
        group = doc[start:start + size].tolist()
        shared.update((a, b) for k, a in enumerate(group) for b in group[k + 1:])
    return shared


def _rank(book):
    """Best copy first: format, then readable Vietnamese share (to 2 decimals: 0.987 vs 0.991 is noise), then a
    file name with Vietnamese diacritics, then more folder levels (folders become genre tags), then length."""
    path, (ext, vi_known, chars), _ = book
    name = path.rsplit("/", 1)[-1]
    return (FORMAT_RANK.get(ext, 9), -round(vi_known or 0, 2), not any(ord(c) > 127 for c in name),
            -path.count("/"), -(chars or 0), len(path))


def find(books):
    """(copy groups: lists of paths, best first; containments: (smaller, bigger, share))."""
    samples = [b[2] for b in books]
    parent = list(range(len(books)))

    def root(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    contained = []
    for (a, b), n in pairs(samples).items():
        sa, sb = len(samples[a]), len(samples[b])
        if min(sa, sb) < MIN_SAMPLE:
            continue
        if n / (sa + sb - n) >= MIN_JACCARD:
            parent[root(a)] = root(b)
        elif n / min(sa, sb) >= MIN_CONTAINED:
            small, big = (a, b) if sa < sb else (b, a)
            contained.append((books[small][0], books[big][0], round(n / min(sa, sb), 2)))
    groups = defaultdict(list)
    for i in range(len(books)):
        groups[root(i)].append(books[i])
    copies = [[b[0] for b in sorted(g, key=_rank)] for g in groups.values() if len(g) > 1]
    return sorted(copies), sorted(contained)


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default="D:/Book")
    parser.add_argument("--delete", action="store_true", help="send the non-kept copies to the Recycle Bin")
    args = parser.parse_args()
    books = list(_books())
    copies, contained = find(books)
    extra = [p for g in copies for p in g[1:]]
    md = ["# Sách trùng nội dung", "", f"{len(books)} cuốn có chữ được so; {len(copies)} nhóm trùng, "
          f"{len(extra)} bản thừa{' (đã chuyển vào Thùng rác)' if args.delete else ''}.", "",
          "## Nhóm trùng (giữ dòng đầu)", ""]
    for g in copies:
        md += [f"- giữ `{g[0]}`"] + [f"  - bỏ `{p}`" for p in g[1:]]
    md += ["", "## Nằm trong cuốn khác (không xóa)", "", "| Cuốn nhỏ | Nằm trong | Tỷ lệ |", "|---|---|---|"]
    md += [f"| {s} | {b} | {r} |" for s, b, r in contained]
    stamp = f"-deleted-{datetime.now():%Y%m%d-%H%M}" if args.delete else ""  # keep the record of what was removed
    out = DATA_ROOT / "reports" / "books" / f"near-dup{stamp}.md"
    out.write_text("\n".join(md) + "\n", encoding="utf-8")
    failed = recycle([Path(args.root) / p for p in extra]) if args.delete else []
    print(json.dumps({"books": len(books), "groups": len(copies), "extra": len(extra), "contained": len(contained),
                      "delete_failed": [str(p) for p in failed]}, ensure_ascii=False))
    print(out)


if __name__ == "__main__":
    main()
