"""
Export Vietnamese Wikimedia sister dumps (20261101) to canonical DocumentRecords:
- vi_wiktionary_org (viwiktionary)
- vi_wikisource_org (viwikisource: ns=0 prose/poems + ns=104 proofread Trang: books grouped per book)
- vi_wikibooks_org  (viwikibooks)
- vi_wikiquote_org  (viwikiquote)
- vi_wikivoyage_org (viwikivoyage)

    uv run python -m pipeline.ingest.wikimedia_dump export [--workers 4] [source_ids...]
"""
import argparse
import bz2
import hashlib
import json
import re
import sys
import unicodedata
import xml.etree.ElementTree as ET
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime
from urllib.parse import quote

import mwparserfromhell

from pipeline.ingest.canonical_writer import ShardWriter
from pipeline.ingest.wikipedia_dump import _pages, strip_balanced, wikitext_to_text
from pipeline.paths import RAW_DIR, REPORTS_DIR
from pipeline.schema.document_record import era_for, make_doc_id, text_sha256, validate

SNAPSHOT = "20261101"
EXTRACTOR = f"wikimedia_dump+mwparserfromhell@{mwparserfromhell.__version__}"

PROJECTS = {
    "vi_wiktionary_org": {
        "file": "viwiktionary-20261101-pages-articles.xml.bz2",
        "sha1": "76da3681f3249f6c08bacae1e87bb4a879ee70eb",
        "base_url": "https://vi.wiktionary.org/wiki/",
    },
    "vi_wikisource_org": {
        "file": "viwikisource-20261101-pages-articles.xml.bz2",
        "sha1": "ae359dd3cb1f689990e86ddb0162544a311d6ae4",
        "base_url": "https://vi.wikisource.org/wiki/",
    },
    "vi_wikibooks_org": {
        "file": "viwikibooks-20261101-pages-articles.xml.bz2",
        "sha1": "0944539f640108df8acaa9dacf94105f0aa55ec7",
        "base_url": "https://vi.wikibooks.org/wiki/",
    },
    "vi_wikiquote_org": {
        "file": "viwikiquote-20261101-pages-articles.xml.bz2",
        "sha1": "e7e4b28f2e0740e53269ff77c6ec66ac8c9a7387",
        "base_url": "https://vi.wikiquote.org/wiki/",
    },
    "vi_wikivoyage_org": {
        "file": "viwikivoyage-20261101-pages-articles.xml.bz2",
        "sha1": "ba8a81aaeb1c43cc8a9ae314a95ba902d222cb81",
        "base_url": "https://vi.wikivoyage.org/wiki/",
    },
}

_POEM_TAG_RE = re.compile(r"</?poem[^>]*>", re.IGNORECASE)
_NOINCLUDE_RE = re.compile(r"<noinclude>.*?</noinclude>", re.DOTALL | re.IGNORECASE)
_WRAPPER_TAG_RE = re.compile(r"</?(?:onlyinclude|includeonly|section)[^>]*>", re.IGNORECASE)
_WS_TRANSCLUDE_RE = re.compile(r"<pages\b[^>]*>", re.IGNORECASE)

_WIKT_VI_HEADER_RE = re.compile(
    r"(?:^\s*\{\{-vie-\}\}|^==\s*\{\{langname\|vi\}\}\s*==|^==\s*Tiếng Việt\s*==)",
    re.MULTILINE | re.IGNORECASE,
)
_WIKT_SECTION_CANDIDATE_RE = re.compile(
    r"(?:^\s*\{\{-(?P<dash>[a-zA-Z0-9 _-]+)-\}\}\s*$|^==[^=].*?==\s*$)",
    re.MULTILINE,
)
_WIKT_SEC_MAP = {
    "etym": "Từ nguyên", "etymology": "Từ nguyên",
    "noun": "Danh từ", "n": "Danh từ", "pr-noun": "Danh từ riêng",
    "verb": "Động từ", "v": "Động từ", "tr-verb": "Ngoại động từ", "intr-verb": "Nội động từ",
    "adj": "Tính từ", "a": "Tính từ", "adjc": "Tính từ",
    "adv": "Phó từ", "adverb": "Phó từ", "pron": "Phát âm",
    "pronoun": "Đại từ", "per-pronoun": "Đại từ nhân xưng",
    "prep": "Giới từ", "conj": "Liên từ", "interj": "Thán từ", "num": "Số từ",
    "usage": "Cách dùng", "syn": "Đồng nghĩa", "synonym": "Đồng nghĩa", "ant": "Trái nghĩa",
    "derived": "Từ dẫn xuất", "drv": "Từ dẫn xuất", "rel": "Từ liên quan", "related": "Từ liên quan",
    "idioms": "Thành ngữ", "expr": "Thành ngữ", "compound": "Từ ghép",
    "paro": "Từ gần âm", "homo": "Từ đồng âm", "forms": "Dạng khác", "alternative form": "Biến thể",
    "desc": "Hậu duệ", "hanviet": "Hán-Việt", "hanviet-t": "Hán-Việt",
    "trans": "Dịch", "translit": "Chuyển tự", "ref": "Tham khảo", "reference": "Tham khảo",
    "see also": "Xem thêm", "nôm": "Chữ Nôm", "info": "", "vi-m": "",
}
_WIKT_NON_LANG_SECS = frozenset(_WIKT_SEC_MAP) | {"vie"}
_WIKT_DROP_SECS = {"pron", "trans", "ref", "reference", "translit", "further"}
_WIKT_SEC_TPL_RE = re.compile(
    r"^(?:=*\s*)?\{\{(?:-(?P<dash>[a-zA-Z0-9 _-]+)-|(?:section|ĐM)\|(?P<arg>[^}|]+))\}\}(?:\s*=*)?$",
    re.MULTILINE,
)


def _nfc(text):
    return text if unicodedata.is_normalized("NFC", text) else unicodedata.normalize("NFC", text)


def sister_wikitext_to_text(wikitext):
    """Clean Wikisource/Wikibooks/Wikiquote/Wikivoyage markup while keeping <poem> lines."""
    cleaned = _NOINCLUDE_RE.sub("", wikitext)
    cleaned = _WRAPPER_TAG_RE.sub("", cleaned)
    cleaned = _POEM_TAG_RE.sub("\n", cleaned)
    cleaned = _WS_TRANSCLUDE_RE.sub("", cleaned)
    return wikitext_to_text(cleaned)


def _extract_vi_wiktionary_block(wikitext):
    """Return only the Vietnamese language section if present, else ''."""
    m = _WIKT_VI_HEADER_RE.search(wikitext)
    if not m:
        return ""
    rest = wikitext[m.end():]
    for cand in _WIKT_SECTION_CANDIDATE_RE.finditer(rest):
        dash = cand.group("dash")
        if dash is None or dash.strip().lower() not in _WIKT_NON_LANG_SECS:
            return rest[:cand.start()]
    return rest


def _replace_wikt_inline_templates(text):
    """Convert common vi.wiktionary inline templates into readable Vietnamese prose."""
    def _sub_tpl(match):
        inner = match.group(1)
        parts = [p.strip() for p in inner.split("|")]
        name = parts[0].lower()
        args = [p for p in parts[1:] if "=" not in p]
        kv = {k.strip(): v.strip() for p in parts[1:] if "=" in p for k, _, v in [p.partition("=")]}
        if name in ("vi-pron", "vie-pron", "wp", "senseid", "đầu", "giữa", "cuối", "mid", "trans-top", "trans-bottom"):
            return ""
        if name.startswith("r:"):
            return ""
        if name == "vi-etym-sino" and args:
            return f"Hán-Việt: {', '.join(args)}"
        if name in ("com", "ghép") and len(args) >= 2:
            return " + ".join(args[1:])
        if name == "bor" and len(args) >= 3:
            return f"mượn từ {args[1]}: {args[2]}"
        if name in ("l", "m", "vi-l", "term", "w"):
            return kv.get("alt") or (args[-1] if args else "")
        if name == "ux" and args:
            return args[-1]
        if name == "like-entry" and args:
            return f"Xem: {args[0]}"
        if name in ("q", "qual", "label", "lbl", "context"):
            vals = [a for a in args if a not in ("vi", "vie")]
            return f"({', '.join(vals)})" if vals else ""
        if name in ("vi-noun", "vi-verb", "vi-adj", "vi-pr-noun"):
            nom = [a for a in args if a and not a.isascii()]
            return f"(Chữ Nôm: {nom[0]})" if nom else ""
        return ""

    # Resolve innermost non-nested {{...}} first, up to 3 passes.
    cur = text
    for _ in range(3):
        nxt = re.sub(r"\{\{([^{}]+)\}\}", _sub_tpl, cur)
        if nxt == cur:
            break
        cur = nxt
    return strip_balanced(cur, "{{")


def wiktionary_to_text(title, wikitext):
    """Extract clean Vietnamese dictionary entry text from a viwiktionary page."""
    block = _extract_vi_wiktionary_block(wikitext)
    if not block:
        return ""
    lines_out = [f"{title} (Tiếng Việt)"]
    dropping = False
    for raw_line in block.splitlines():
        m = _WIKT_SEC_TPL_RE.match(raw_line.strip())
        if m:
            sec_key = (m.group("dash") or m.group("arg") or "").strip().lower()
            dropping = sec_key in _WIKT_DROP_SECS
            if not dropping:
                label = _WIKT_SEC_MAP.get(sec_key, sec_key)
                if label:
                    lines_out.append(f"\n{label}:")
            continue
        if dropping:
            continue
        lines_out.append(raw_line)
    joined = _replace_wikt_inline_templates("\n".join(lines_out))
    joined = re.sub(r"<gallery[^>]*>.*?</gallery>", "", joined, flags=re.DOTALL | re.IGNORECASE)
    joined = re.sub(r"<ref[^>]*>.*?</ref>|<ref[^/>]*/>", "", joined, flags=re.DOTALL | re.IGNORECASE)
    code = mwparserfromhell.parse(strip_balanced(joined, "{|"))
    for link in code.filter_wikilinks():
        if str(link.title).strip().lower().startswith(("tập tin:", "hình:", "file:", "image:", "thể loại:", "category:")):
            try:
                code.remove(link)
            except ValueError:
                pass
    text = code.strip_code(normalize=True, collapse=True)
    cleaned_lines = [line.strip() for line in text.splitlines() if line.strip()]
    out = _nfc("\n".join(cleaned_lines)).strip()
    # Skip entries that only have the title + empty section headers
    meaningful = [l for l in cleaned_lines[1:] if not l.endswith(":")]
    return out if meaningful else ""


def _to_record(args):
    source_id, base_url, dump_name, (page_id, rev_id, title, timestamp, wikitext) = args
    if source_id == "vi_wiktionary_org":
        text = wiktionary_to_text(title, wikitext)
    else:
        text = sister_wikitext_to_text(wikitext)
    if not text or len(text) < 40:
        return None
    url = base_url + quote(title.replace(" ", "_"))
    revised = datetime.fromisoformat(timestamp.replace("Z", "+00:00")).replace(tzinfo=None)
    return {
        "doc_id": make_doc_id(source_id, f"{url}@{SNAPSHOT}"),
        "source_id": source_id,
        "source_type": "reference",
        "url": url,
        "title": _nfc(title),
        "text": text,
        "lang": None,
        "lang_score": None,
        "publish_date": revised.date(),
        "publish_date_source": "dump",
        "crawl_time": datetime.strptime(SNAPSHOT, "%Y%m%d"),
        "era": era_for(revised.date()),
        "author": None,
        "category": None,
        "tags": [],
        "raw_ref": f"raw/{source_id}/{dump_name}#page={page_id}",
        "text_sha256": text_sha256(text),
        "license": "cc-by-sa-4.0",
        "usage_scope": "train_ok",
        "extractor": EXTRACTOR,
        "meta": json.dumps({"page_id": page_id, "rev_id": rev_id, "snapshot": SNAPSHOT}),
    }


def _iter_ns104_books(path):
    """Group viwikisource ns=104 (Trang:<book>/<page>) into per-book tuples."""
    books = defaultdict(list)
    for _, elem in ET.iterparse(bz2.open(path), events=("end",)):
        if elem.tag.rsplit("}", 1)[-1] != "page":
            continue
        ns = elem.tag[:-len("page")]
        if elem.findtext(f"{ns}ns") == "104" and elem.find(f"{ns}redirect") is None:
            title = elem.findtext(f"{ns}title") or ""
            rev = elem.find(f"{ns}revision")
            wikitext = rev.findtext(f"{ns}text") or ""
            ts = rev.findtext(f"{ns}timestamp")
            if title.startswith("Trang:") and "/" in title:
                book, _, pg_str = title[len("Trang:"):].rpartition("/")
                try:
                    pg_num = int(pg_str)
                except ValueError:
                    pg_num = 0
                books[book].append((pg_num, ts, wikitext))
        elem.clear()
    return books


def export_source(source_id, workers=4):
    cfg = PROJECTS[source_id]
    path = RAW_DIR / source_id / cfg["file"]
    if not path.exists():
        raise FileNotFoundError(f"Missing dump: {path}")
    writer = ShardWriter(source_id, "dump")
    stats = {"pages": 0, "written": 0, "empty": 0, "invalid": 0, "books_ns104": 0, "era": {}}
    try:
        with ProcessPoolExecutor(max_workers=min(workers, 4)) as pool:
            jobs = ((source_id, cfg["base_url"], path.name, page) for page in _pages(path))
            for record in pool.map(_to_record, jobs, chunksize=128):
                stats["pages"] += 1
                if record is None:
                    stats["empty"] += 1
                elif validate(record):
                    stats["invalid"] += 1
                else:
                    writer.write(record)
                    stats["written"] += 1
                    stats["era"][record["era"]] = stats["era"].get(record["era"], 0) + 1
                if stats["pages"] % 25000 == 0:
                    print(f"{source_id}: {stats}", flush=True)

        if source_id == "vi_wikisource_org":
            books = _iter_ns104_books(path)
            for book_name, pages in books.items():
                pages.sort(key=lambda x: x[0])
                parts, latest_ts = [], None
                for _, ts, wt in pages:
                    cleaned = sister_wikitext_to_text(wt)
                    if cleaned and len(cleaned) >= 20:
                        parts.append(cleaned)
                        if latest_ts is None or (ts and ts > latest_ts):
                            latest_ts = ts
                text = _nfc("\n\n".join(parts)).strip()
                if len(text) < 200:
                    continue
                revised = datetime.fromisoformat((latest_ts or "2026-11-01T00:00:00Z").replace("Z", "+00:00")).replace(tzinfo=None)
                url = cfg["base_url"] + "Mục_lục:" + quote(book_name.replace(" ", "_"))
                rec = {
                    "doc_id": make_doc_id(source_id, f"{url}@{SNAPSHOT}#ns104"),
                    "source_id": source_id,
                    "source_type": "reference",
                    "url": url,
                    "title": _nfc(book_name.removesuffix(".pdf").removesuffix(".djvu")),
                    "text": text,
                    "lang": None,
                    "lang_score": None,
                    "publish_date": revised.date(),
                    "publish_date_source": "dump",
                    "crawl_time": datetime.strptime(SNAPSHOT, "%Y%m%d"),
                    "era": era_for(revised.date()),
                    "author": None,
                    "category": "wikisource_book",
                    "tags": [],
                    "raw_ref": f"raw/{source_id}/{path.name}#ns104:{book_name}",
                    "text_sha256": text_sha256(text),
                    "license": "cc-by-sa-4.0",
                    "usage_scope": "train_ok",
                    "extractor": EXTRACTOR,
                    "meta": json.dumps({"namespace": 104, "pages": len(parts), "snapshot": SNAPSHOT}, ensure_ascii=False),
                }
                if not validate(rec):
                    writer.write(rec)
                    stats["written"] += 1
                    stats["books_ns104"] += 1
                    stats["era"][rec["era"]] = stats["era"].get(rec["era"], 0) + 1
        writer.commit()
    except BaseException:
        writer.abort()
        raise
    (REPORTS_DIR / "export").mkdir(parents=True, exist_ok=True)
    (REPORTS_DIR / "export" / f"{source_id}.json").write_text(json.dumps(stats, indent=1, ensure_ascii=False), encoding="utf-8")
    return stats


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("sources", nargs="*", default=list(PROJECTS))
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args()
    for sid in args.sources:
        if sid not in PROJECTS:
            parser.error(f"unknown source: {sid}")
        st = export_source(sid, args.workers)
        print(f"DONE {sid}: {json.dumps(st, ensure_ascii=False)}")


if __name__ == "__main__":
    main()
