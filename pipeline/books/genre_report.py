"""
Phase 7: genre breakdown of the book collection and the OCR order for scans, from the `genres`
table written by pipeline.books.classify_genre.

    uv run python -m pipeline.books.genre_report

Writes DATA_ROOT/reports/books/genres.md (field x form tables, extracted chars and scan pages) and
DATA_ROOT/reports/books/ocr-priority.csv (every scan with its tier; OCR runs tier 1 first).
"""
import csv
import sqlite3
import sys
from collections import defaultdict

from pipeline.books.classify_genre import FIELDS, FORMS
from pipeline.books.inventory import DB_PATH
from pipeline.paths import DATA_ROOT

OUT = DATA_ROOT / "reports" / "books"
# Tier 3: OCR gives little: foreign-language scans (the web and open datasets have far more), periodicals
# (old orthography, poor print, mostly in the 1929 folder) and dictionaries (lists, not prose).
# Tier 2: translated chronicles need layout review (notes, Hán text beside quốc ngữ); unparsed replies.
LATER_FORMS = {"bao_tap_chi", "tu_dien"}
REVIEW_FORMS = {"su_lieu_dich"}


def tier(field, form, lang):
    if lang is not None and lang != "vi" or form in LATER_FORMS:
        return 3
    if None in (field, form, lang) or form in REVIEW_FORMS:
        return 2
    return 1


# The model labels many Vietnamese translations by the author's nationality ("Xứ Đông Dương - Paul Doumer"
# -> fr). For books with text, the measured Vietnamese syllable share decides instead.
VI_KNOWN = 0.8


def _rows(con):
    return con.execute("SELECT f.path, f.status, coalesce(f.chars, 0), coalesce(f.pages, 0), g.field, g.form, "
                       "CASE WHEN f.vi_known IS NULL THEN g.lang WHEN f.vi_known >= ? THEN 'vi' "
                       "WHEN g.lang = 'vi' THEN 'khac' ELSE g.lang END, g.basis "
                       "FROM files f JOIN genres g ON g.sha256 = f.sha256 "
                       "WHERE f.status IN ('extracted', 'ocr_pending') ORDER BY f.path", (VI_KNOWN,)).fetchall()


def _table(title, key, labels, rows):
    agg = defaultdict(lambda: [0, 0, 0, 0])
    for path, status, chars, pages, field, form, lang, _ in rows:
        a = agg[key(field, form, lang)]
        if status == "extracted":
            a[0] += 1
            a[1] += chars
        else:
            a[2] += 1
            a[3] += pages
    lines = [f"## {title}", "", "| Nhóm | Đã trích | Triệu ký tự | Scan chờ OCR | Trang scan |", "|---|---|---|---|---|"]
    for k, (n, chars, s, pages) in sorted(agg.items(), key=lambda kv: -(kv[1][1] + kv[1][3] * 2000)):
        lines.append(f"| {labels.get(k, k) if k else '(không đọc được)'} | {n} | {chars / 1e6:.1f} | {s} | {pages:,} |")
    return lines + [""]


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    con = sqlite3.connect(DB_PATH)
    rows = _rows(con)
    field_labels = {k: f"{k} ({v.split(':')[0].split(',')[0]})" for k, v in FIELDS.items()}
    form_labels = {k: f"{k} ({v.split(',')[0]})" for k, v in FORMS.items()}
    scans = sorted(((tier(f, fo, la), p, pg, f, fo, la) for p, s, _, pg, f, fo, la, _ in rows if s == "ocr_pending"),
                   key=lambda r: (r[0], r[3] or "", r[1]))
    tiers = defaultdict(lambda: [0, 0])
    for t, _, pages, *_ in scans:
        tiers[t][0] += 1
        tiers[t][1] += pages
    md = ["# Thể loại sách và thứ tự OCR", "",
          f"{len(rows)} cuốn đã phân loại (sách đã trích: theo tên + đoạn trích; sách scan: chỉ theo tên, "
          "metadata, mục lục PDF nên kém chắc chắn hơn).", ""]
    md += _table("Theo lĩnh vực", lambda f, fo, la: f, field_labels, rows)
    md += _table("Theo thể loại", lambda f, fo, la: fo, form_labels, rows)
    md += _table("Theo ngôn ngữ", lambda f, fo, la: la, {}, rows)
    md += ["## Thứ tự OCR", "", "| Đợt | Cuốn | Trang |", "|---|---|---|"]
    md += [f"| {t} | {n} | {pages:,} |" for t, (n, pages) in sorted(tiers.items())] + ["", "Danh sách: ocr-priority.csv"]
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "genres.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    with open(OUT / "ocr-priority.csv", "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["tier", "path", "pages", "field", "form", "lang"])
        w.writerows(scans)
    print(OUT / "genres.md")


if __name__ == "__main__":
    main()
