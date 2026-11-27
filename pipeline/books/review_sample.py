"""
Reading check of extracted books (phase 7 step 2: a person reads 20 books' excerpts).

    uv run python -m pipeline.books.review_sample [--n 20]

Picks books by a salted hash, converted TCVN3/VNI books first (the riskiest path), then the rest,
and writes REPORTS_DIR/books/review-<n>.md: per book its quality numbers and three excerpts
(near the start, the middle, near the end) to compare against the PDF.
"""
import argparse
import glob
import hashlib
import json
import sys

import pyarrow.parquet as pq

from pipeline.paths import CANONICAL_DIR, REPORTS_DIR

SALT = "books-review/v1"
EXCERPT = 900
AT = (0.08, 0.5, 0.9)


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("--n", type=int, default=20)
    args = parser.parse_args()
    files = glob.glob(str(CANONICAL_DIR / "owner_books" / "book" / "*.parquet"))
    books = pq.read_table(files, columns=["title", "raw_ref", "text", "meta"]).to_pylist()
    rank = lambda b: hashlib.sha256(f"{SALT}:{b['raw_ref']}".encode()).hexdigest()  # noqa: E731
    legacy = sorted((b for b in books if json.loads(b["meta"])["encoding"] in ("tcvn3", "vni")), key=rank)
    rest = sorted((b for b in books if json.loads(b["meta"])["encoding"] not in ("tcvn3", "vni")), key=rank)
    picked = legacy[: max(4, args.n // 4)] + rest
    lines = [f"# Đọc kiểm tra {args.n} cuốn sách đã trích", "",
             "Mỗi cuốn: 3 đoạn (gần đầu, giữa, gần cuối). Ghi lại lỗi gặp: mất dấu, sai chữ, dòng bị ngắt giữa câu, "
             "header/số trang còn sót, đoạn lặp.", ""]
    for b in picked[: args.n]:
        meta, text = json.loads(b["meta"]), b["text"]
        lines += [f"## {b['title']}", "",
                  f"`{b['raw_ref']}` · bảng mã gốc: {meta['encoding']} · {meta['pages']} trang "
                  f"({meta['image_pages']} trang ảnh) · {len(text):,} ký tự · âm tiết hợp lệ: {meta['vi_known']}", ""]
        for at in AT:
            start = int(len(text) * at)
            lines += ["```text", text[start:start + EXCERPT].strip(), "```", ""]
    out = REPORTS_DIR / "books" / f"review-{args.n}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines), encoding="utf-8")
    print(out)


if __name__ == "__main__":
    main()
