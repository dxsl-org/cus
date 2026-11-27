"""Markdown summary of books.sqlite (pipeline.books.inventory)."""
from collections import Counter, defaultdict
from pathlib import PurePosixPath

GB = 1e9


def _table(header, rows):
    out = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    out += ["| " + " | ".join(str(c) for c in r) + " |" for r in rows]
    return "\n".join(out)


def render(con, root):
    rows = [dict(zip([d[0] for d in cur.description], r)) for cur in [con.execute("SELECT * FROM files")] for r in cur]
    unique = [r for r in rows if not r["duplicate_of"]]
    dup = [r for r in rows if r["duplicate_of"]]
    parts = [f"# Kiểm kê sách: `{root}`", "",
             f"{len(rows)} file, {sum(r['size'] for r in rows) / GB:.1f} GB; trùng nội dung (sha256): {len(dup)} file, "
             f"{sum(r['size'] for r in dup) / GB:.1f} GB.", "", "## Theo loại (không tính bản trùng)", ""]
    by_kind = defaultdict(lambda: [0, 0])
    for r in unique:
        by_kind[r["kind"]][0] += 1
        by_kind[r["kind"]][1] += r["size"]
    parts.append(_table(["Loại", "File", "GB"], [(k, n, f"{s / GB:.1f}") for k, (n, s) in sorted(by_kind.items())]))

    pdfs = [r for r in unique if r["kind"] == "pdf"]
    groups = Counter((r["pdf_kind"] or "error", r["encoding"] or "—") for r in pdfs)
    pages = defaultdict(int)
    for r in pdfs:
        pages[(r["pdf_kind"] or "error", r["encoding"] or "—")] += r["pages"] or 0
    parts += ["", "## PDF: lớp chữ và bảng mã", "",
              "`pdf_text`: mọi trang mẫu có chữ; `pdf_mixed`: một phần; `pdf_scan`: chỉ ảnh, cần OCR. "
              "Bảng mã `tcvn3` / `vni` cần chuyển sang Unicode trước khi dùng.", "",
              _table(["Lớp chữ", "Bảng mã", "File", "Trang"],
                     [(k, e, n, pages[(k, e)]) for (k, e), n in sorted(groups.items())])]
    errors = [r for r in unique if r["error"]]
    if errors:
        parts += ["", f"## Lỗi đọc ({len(errors)})", "",
                  _table(["File", "Lỗi"], [(r["path"], r["error"]) for r in errors[:100]])]

    image_dirs = Counter(str(PurePosixPath(r["path"]).parent) for r in unique if r["kind"] == "image")
    parts += ["", f"## Thư mục ảnh ({sum(image_dirs.values())} ảnh, {len(image_dirs)} thư mục)", "",
              "Cần người xác nhận thư mục nào là trang sách chụp/scan (đưa vào OCR sau), thư mục nào là ảnh minh hoạ.", "",
              _table(["Thư mục", "Ảnh"], image_dirs.most_common(40))]
    other = Counter(r["ext"] for r in unique if r["kind"] in ("other", "archive", "djvu"))
    parts += ["", "## Định dạng chưa xử lý", "", _table(["Đuôi", "File"], other.most_common())]
    return "\n".join(parts) + "\n"
