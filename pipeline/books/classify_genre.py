"""
Phase 7: field, form and language of every book (extracted text PDFs and OCR-pending scans) by the
local Qwen model, so OCR can be ordered by value instead of by guesswork on file names.

    uv run --group eval python -m pipeline.books.classify_genre [--batch-size 8] [--limit N]

Evidence given to the model: file name, folder, page count, PDF metadata and bookmarks, plus for
extracted books an opening and a mid-book excerpt. Scans have no text yet (no OCR engine on this
machine), so their row says basis='title' and is less certain. Results go to the `genres` table of
books.sqlite, keyed by sha256: classified books are skipped on re-run. A reply whose codes are not
in the lists below is stored with nulls, never guessed. Run pipeline.books.genre_report afterwards.
"""
import argparse
import re
import sqlite3
import sys
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

import pyarrow.parquet as pq
import pymupdf

from pipeline.books.inventory import DB_PATH
from pipeline.paths import DATA_ROOT

FIELDS = {"van_hoc": "văn học sáng tác: tiểu thuyết, truyện, thơ, kịch, tản văn, hồi ký",
          "nghien_cuu_van_hoc": "nghiên cứu, phê bình, lịch sử văn học",
          "lich_su": "lịch sử, sử liệu, chiến tranh, nhân vật lịch sử, lịch sử kinh tế",
          "van_hoa": "văn hóa, dân tộc học, phong tục, lễ hội, ẩm thực",
          "nghe_thuat": "mỹ thuật, điêu khắc, kiến trúc, âm nhạc, sân khấu, cổ vật",
          "dia_chi": "địa chí, địa lý, du lịch, vùng đất, biển đảo",
          "ton_giao": "tôn giáo, tín ngưỡng, triết học, tử vi",
          "luat_chinh_tri": "pháp luật, chính trị, nhà nước, quan hệ quốc tế",
          "kinh_te": "kinh tế học, kinh doanh, thương hiệu, marketing, quản trị, tài chính",
          "ky_nang": "phát triển bản thân, kỹ năng sống, làm giàu",
          "khoa_hoc_ky_thuat": "toán, vật lý, hóa, sinh, kỹ thuật, công nghệ thông tin, nông nghiệp",
          "y_te": "y học, sức khỏe",
          "giao_duc_xa_hoi": "giáo dục, tâm lý học, xã hội học",
          "ngon_ngu": "ngôn ngữ học, từ điển, Hán Nôm",
          "khac": "không thuộc nhóm nào ở trên"}
FORMS = {"tieu_thuyet": "tiểu thuyết, truyện dài", "truyen_ngan": "truyện ngắn, tập truyện",
         "tho": "thơ, ca dao", "hoi_ky": "hồi ký, ký sự, tự truyện, bút ký",
         "chuyen_khao": "sách chuyên khảo, nghiên cứu, luận án", "pho_thong": "sách phổ thông, sách kỹ năng",
         "giao_trinh": "giáo trình, sách giáo khoa, cẩm nang", "su_lieu_dich": "bản dịch sử liệu, văn bản cổ",
         "ky_yeu": "kỷ yếu hội thảo, tuyển tập nhiều bài", "bai_bao": "một bài báo hay bài nghiên cứu ngắn",
         "bao_tap_chi": "một số báo, tạp chí", "tu_dien": "từ điển, tra cứu", "khac": "khác"}
LANGS = ("vi", "en", "fr", "zh", "khac")
EXCERPT = 900
# Evidence first and a one-line summary before the codes: in the 16-book trial, codes chosen straight
# after a long option list were often wrong (art books as engineering, a branding book as a novel).
PROMPT = """Thông tin một tài liệu trong thư viện:
{evidence}

Hãy phân loại tài liệu trên. Tên có "số <n>" kèm ngày tháng thường là một số báo hoặc tạp chí.
Chỉ dùng "su_lieu_dich" cho bản dịch văn bản cổ như chính sử, châu bản, địa bạ.

Mã lĩnh vực:
{fields}

Mã thể loại:
{forms}

Mã ngôn ngữ chính: vi, en, fr, zh, khac. Đây là ngôn ngữ của văn bản trong file, không phải quốc tịch tác giả:
sách dịch sang tiếng Việt là vi; tên file tiếng Việt thường là sách tiếng Việt.

Trả lời đúng 4 dòng:
Tóm tắt: <tài liệu này nói về gì, tối đa 20 từ>
Lĩnh vực: <một mã lĩnh vực>
Thể loại: <một mã thể loại>
Ngôn ngữ: <một mã ngôn ngữ>"""
SCHEMA = """CREATE TABLE IF NOT EXISTS genres (sha256 TEXT PRIMARY KEY, path TEXT, basis TEXT, field TEXT,
    form TEXT, lang TEXT, reply TEXT, model TEXT, classified_at TEXT)"""


def _codes(options):
    return "\n".join(f"- {k}: {v}" for k, v in options.items())


def parse(reply):
    """(field, form, lang) from the reply; a code outside its list becomes None."""
    def pick(label, allowed):
        m = re.findall(rf"{label}\s*:\s*([a-z_]+)", reply, re.IGNORECASE)
        return m[-1].lower() if m and m[-1].lower() in allowed else None
    return pick("Lĩnh vực", FIELDS), pick("Thể loại", FORMS), pick("Ngôn ngữ", LANGS)


def _pdf_facts(path):
    try:
        with pymupdf.open(path) as doc:
            meta = doc.metadata or {}
            toc = [t[1] for t in doc.get_toc()[:15]]
    except Exception:
        return []
    facts = [f"{k}: {' '.join(str(meta.get(k) or '').split())}" for k in ("title", "author", "subject", "keywords")
             if (meta.get(k) or "").strip()]
    return facts + ([f"mục lục: {' | '.join(' '.join(t.split()) for t in toc)}"] if toc else [])


def _excerpts(text):
    middle = len(text) // 3
    return [f"đoạn đầu: {' '.join(text[:EXCERPT].split())}",
            f"đoạn giữa sách: {' '.join(text[middle:middle + EXCERPT].split())}"]


def evidence(root, row, text):
    parts = [f"tên file: {unicodedata.normalize('NFC', Path(row['path']).stem)}",
             f"thư mục: {row['path'].rsplit('/', 1)[0] if '/' in row['path'] else '(gốc)'}",
             f"số trang: {row['pages']}"] + _pdf_facts(Path(root) / row["path"])
    return "\n".join(parts + (_excerpts(text) if text else ["(chưa có nội dung, hãy phân loại theo tên file và thư mục)"]))


def _texts():
    folder = DATA_ROOT / "canonical" / "owner_books"
    texts = {}
    for part in sorted(folder.glob("[!.]*/*.parquet")):  # book/ (PDF) and ebook/ (other formats)
        for batch in pq.ParquetFile(part).iter_batches(columns=["raw_ref", "text"], batch_size=64):
            for ref, text in zip(batch.column("raw_ref").to_pylist(), batch.column("text").to_pylist()):
                texts[ref.removeprefix("file:")] = text
    return texts


def run(root, batch_size, limit, model_name):
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    con.execute(SCHEMA)
    rows = [dict(r) for r in con.execute(
        "SELECT path, sha256, pages, status FROM files WHERE status IN ('extracted', 'ocr_pending') "
        "AND sha256 NOT IN (SELECT sha256 FROM genres) ORDER BY path")][: limit or None]
    texts = _texts()
    print(f"{len(rows)} books to classify", flush=True)
    from pipeline.quality.label_llm import _load, generate  # torch: eval group only, not needed by the report
    tok, model = _load(model_name)
    prompt = PROMPT.replace("{fields}", _codes(FIELDS)).replace("{forms}", _codes(FORMS))
    unparsed = 0
    for i in range(0, len(rows), batch_size):
        batch = rows[i:i + batch_size]
        messages = [prompt.replace("{evidence}", evidence(root, r, texts.get(r["path"]))) for r in batch]
        for r, reply in zip(batch, generate(tok, model, messages, max_new_tokens=90)):
            field, form, lang = parse(reply)
            unparsed += None in (field, form, lang)
            con.execute("INSERT OR REPLACE INTO genres VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (r["sha256"], r["path"], "text" if r["path"] in texts else "title", field, form, lang,
                         reply.strip()[:300], model_name, datetime.now(timezone.utc).isoformat(timespec="seconds")))
        con.commit()
        print(f"{i + len(batch)}/{len(rows)} unparsed={unparsed}", flush=True)


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default="D:/Book")
    parser.add_argument("--batch-size", type=int, default=8)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--model", default="Qwen/Qwen2.5-7B-Instruct")  # same model as pipeline.quality.label_llm
    args = parser.parse_args()
    run(args.root, args.batch_size, args.limit, args.model)


if __name__ == "__main__":
    main()
