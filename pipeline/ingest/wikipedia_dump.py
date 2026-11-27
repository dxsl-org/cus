"""
Vietnamese Wikipedia from official dumps (replaces the legacy 72k-page crawl).

Two snapshots, both exported into canonical/vi_wikipedia_org:
- 20220501 (archive.org mirror): last snapshot available before ChatGPT.
- 20261101 (dumps.wikimedia.org): current text.
Phase 6 dedups identical articles across snapshots.

    uv run python -m pipeline.ingest.wikipedia_dump download
    uv run python -m pipeline.ingest.wikipedia_dump export

doc_id locator = "<article url>@<snapshot>" so the two versions of an article are
distinct documents. publish_date = date of the revision in the dump (tag "dump").
"""
import argparse
import bz2
import hashlib
import json
import re
import subprocess
import sys
import unicodedata
import xml.etree.ElementTree as ET
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime
from urllib.parse import quote

import mwparserfromhell

from pipeline.ingest.canonical_writer import ShardWriter
from pipeline.paths import RAW_DIR, REPORTS_DIR
from pipeline.schema.document_record import era_for, make_doc_id, text_sha256, validate

SOURCE_ID = "vi_wikipedia_org"
SNAPSHOTS = {
    "20220501": ("https://archive.org/download/viwiki-20220501/viwiki-20220501-pages-articles.xml.bz2",
                 "0aa4b92bcc0dc3b6d2cea058e75184fcc7a1c6c8"),
    "20261101": ("https://dumps.wikimedia.org/viwiki/20261101/viwiki-20261101-pages-articles.xml.bz2",
                 "d983f854bde20b38ad4263649796e96589ceb27c"),
}
EXTRACTOR = f"mwparserfromhell@{mwparserfromhell.__version__}"
_DROP_SECTIONS = ("Tham khảo", "Chú thích", "Liên kết ngoài", "Xem thêm", "Đọc thêm", "Ghi chú")
_BRACES = {"{{": re.compile(r"\{\{|\}\}"), "{|": re.compile(r"\{\||\|\}")}
_EMPTY_PARENS = re.compile(r"\s*\(\s*[,;:]?\s*\)")
_LEADING_PAREN_PUNCT = re.compile(r"\(\s*[,;:]\s*")
_FILE_PREFIXES = ("tập tin:", "file:", "hình:", "image:", "thể loại:", "category:")


def strip_balanced(text, opener):
    """
    Remove balanced {{...}} templates (or {|...|} tables), nesting included.
    Done on raw wikitext before mwparserfromhell: large infoboxes otherwise fail to
    parse as templates and leak into the text, and parsing them dominates runtime.
    Unclosed blocks are left as-is.
    """
    out, depth, start, pos = [], 0, 0, 0
    for m in _BRACES[opener].finditer(text):
        if m.group() == opener:
            if depth == 0:
                out.append(text[pos:m.start()])
                start = m.start()
            depth += 1
        elif depth:
            depth -= 1
            if depth == 0:
                pos = m.end()
    out.append(text[pos:] if depth == 0 else text[start:])
    return "".join(out)


def dump_path(snapshot):
    return RAW_DIR / SOURCE_ID / SNAPSHOTS[snapshot][0].rsplit("/", 1)[1]


def _sha1(path):
    digest = hashlib.sha1()
    with open(path, "rb") as f:
        while chunk := f.read(8 * 2**20):
            digest.update(chunk)
    return digest.hexdigest()


def download(snapshot):
    """Resumable (curl -C -) download, then sha1 check against the pinned checksum."""
    url, sha1 = SNAPSHOTS[snapshot]
    path = dump_path(snapshot)
    if path.exists():
        print(f"{path.name}: present")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    part = path.with_suffix(".part")
    # archive.org throttles and drops long transfers (curl exit 56); resume patiently.
    subprocess.run(["curl", "-fsSL", "--retry", "100", "--retry-all-errors", "--retry-delay", "60",
                    "-C", "-", "-o", str(part), url], check=True)
    if _sha1(part) != sha1:
        part.unlink()
        raise RuntimeError(f"{path.name}: sha1 mismatch")
    part.rename(path)
    print(f"{path.name}: downloaded, sha1 ok")


def wikitext_to_text(wikitext):
    """Plain text: drop templates, refs, tables, files/categories and reference sections."""
    code = mwparserfromhell.parse(strip_balanced(strip_balanced(wikitext, "{{"), "{|"))
    for section in code.get_sections(levels=[2], include_headings=True):
        heading = section.filter_headings()[0].title.strip_code().strip() if section.filter_headings() else ""
        if heading in _DROP_SECTIONS:
            code.remove(section)
    for node in code.filter_templates(recursive=False) + code.filter_tags(matches=lambda t: t.tag in ("ref", "table", "gallery")):
        try:
            code.remove(node)
        except ValueError:
            pass  # already removed with a parent
    for link in code.filter_wikilinks():
        if str(link.title).strip().lower().startswith(_FILE_PREFIXES):
            try:
                code.remove(link)
            except ValueError:
                pass
    text = code.strip_code(normalize=True, collapse=True)
    # Leftovers of dropped pronunciation/lang templates: "X ()" and "X (; 1772–1822)".
    text = _LEADING_PAREN_PUNCT.sub("(", _EMPTY_PARENS.sub("", text))
    lines = [line.strip() for line in text.splitlines()]
    return unicodedata.normalize("NFC", "\n".join(lines)).strip()


def _pages(path):
    """Yield (page_id, rev_id, title, timestamp, wikitext) for main-namespace non-redirect pages."""
    ns = ""
    for _, elem in ET.iterparse(bz2.open(path), events=("end",)):
        tag = elem.tag.rsplit("}", 1)[-1]
        if tag != "page":
            continue
        ns = elem.tag[: -len("page")]
        if elem.findtext(f"{ns}ns") == "0" and elem.find(f"{ns}redirect") is None:
            rev = elem.find(f"{ns}revision")
            yield (elem.findtext(f"{ns}id"), rev.findtext(f"{ns}id"), elem.findtext(f"{ns}title"),
                   rev.findtext(f"{ns}timestamp"), rev.findtext(f"{ns}text") or "")
        elem.clear()


def _to_record(args):
    snapshot, dump_name, (page_id, rev_id, title, timestamp, wikitext) = args
    text = wikitext_to_text(wikitext)
    if not text:
        return None
    url = "https://vi.wikipedia.org/wiki/" + quote(title.replace(" ", "_"))
    revised = datetime.fromisoformat(timestamp.replace("Z", "+00:00")).replace(tzinfo=None)
    return {
        "doc_id": make_doc_id(SOURCE_ID, f"{url}@{snapshot}"), "source_id": SOURCE_ID,
        "source_type": "reference", "url": url, "title": unicodedata.normalize("NFC", title), "text": text,
        "lang": None, "lang_score": None, "publish_date": revised.date(), "publish_date_source": "dump",
        "crawl_time": datetime.strptime(snapshot, "%Y%m%d"), "era": era_for(revised.date()),  # publish date always set
        "author": None, "category": None, "tags": [], "raw_ref": f"raw/{SOURCE_ID}/{dump_name}#page={page_id}",
        "text_sha256": text_sha256(text), "license": "cc-by-sa-4.0", "usage_scope": "train_ok",
        "extractor": EXTRACTOR, "meta": json.dumps({"page_id": page_id, "rev_id": rev_id, "snapshot": snapshot}),
    }


def export(workers):
    writer, stats = ShardWriter(SOURCE_ID, "dump"), {}
    try:
        with ProcessPoolExecutor(max_workers=workers) as pool:
            for snapshot in SNAPSHOTS:
                path = dump_path(snapshot)
                jobs = ((snapshot, path.name, page) for page in _pages(path))
                counts = stats.setdefault(snapshot, {"pages": 0, "written": 0, "empty": 0, "invalid": 0, "era": {}})
                for record in pool.map(_to_record, jobs, chunksize=256):
                    counts["pages"] += 1
                    if record is None:
                        counts["empty"] += 1
                    elif validate(record):
                        counts["invalid"] += 1
                    else:
                        writer.write(record)
                        counts["written"] += 1
                        counts["era"][record["era"]] = counts["era"].get(record["era"], 0) + 1
                    if counts["pages"] % 50000 == 0:
                        print(f"{snapshot}: {counts}", flush=True)
        writer.commit()
    except BaseException:
        writer.abort()
        raise
    (REPORTS_DIR / "export").mkdir(parents=True, exist_ok=True)
    (REPORTS_DIR / "export" / f"{SOURCE_ID}.json").write_text(json.dumps(stats, indent=1), encoding="utf-8")
    print(json.dumps(stats))


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["download", "export"])
    parser.add_argument("--workers", type=int, default=6)
    args = parser.parse_args()
    if args.command == "download":
        for snapshot in SNAPSHOTS:
            download(snapshot)
    else:
        export(args.workers)


if __name__ == "__main__":
    main()
