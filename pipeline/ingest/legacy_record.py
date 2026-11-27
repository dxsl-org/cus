"""
Map one row of the legacy Craw-Web-Data SQLite `articles` table to a DocumentRecord.

Known legacy quirks handled here (measured 2026-11-13):
- thanhnien/tuoitre/nhandan/suckhoedoisong store a local file path in `url`;
  the real URL is metadata.link.
- tuoitre metadata.date is crawl date; the URL id carries the real date
  (handled by date_normalizer URL precedence).
- vbpl content may start with junk "Are You suprised ?".
- toaan text extracted from PDF sometimes has diacritics split onto their own
  lines; flagged as meta.text_issue and usage_scope=blocked until re-extracted.
"""
import json
import unicodedata
from datetime import datetime

from pipeline.ingest.date_normalizer import resolve_publish_date
from pipeline.schema.document_record import (
    SENSITIVE_META_KEYS, canonical_url, era_for, make_doc_id, text_sha256,
)

EXTRACTOR = "legacy-sqlite@1"
BOILERPLATE_PREFIXES = {"vbpl_vn": ("Are You suprised ?",)}
_VI_TONED_VOWELS = frozenset(
    "ảẢốỐầẦấẤậẬẩẨẫẪắẮằẰẳẲẵẴặẶẻẺẽẼẹẸềỀếẾểỂễỄệỆỉỈĩĨịỊỏỎõÕọỌồỒổỔỗỖộỘ"
    "ờỜớỚởỞỡỠợỢủỦũŨụỤừỪứỨửỬữỮựỰỳỲỷỶỹỸỵỴ"
)
BROKEN_DIACRITICS_MIN_LINES = 5


def broken_diacritic_lines(text, limit=5000):
    """Lines consisting of a single toned vowel: signature of PDF text with split diacritics."""
    return sum(1 for line in text[:limit].splitlines() if line.strip() in _VI_TONED_VOWELS)


def _nfc(value):
    return unicodedata.normalize("NFC", value) if isinstance(value, str) else None


def _parse_json(raw):
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return raw


def _tags(raw):
    value = _parse_json(raw)
    if isinstance(value, list):
        return [_nfc(str(t)).strip() for t in value if str(t).strip()]
    if isinstance(value, str) and value.strip():
        return [_nfc(t).strip() for t in value.split(",") if t.strip()]
    return []


def _strip_boilerplate(source_id, text):
    for prefix in BOILERPLATE_PREFIXES.get(source_id, ()):
        if text.startswith(prefix):
            text = text[len(prefix):].lstrip()
    return text


def _locate(row, meta):
    """Return (canonical url | None, locator for doc_id); records legacy local paths in meta."""
    legacy_url = row.get("url") or ""
    link = meta.get("link") if isinstance(meta.get("link"), str) else ""
    if legacy_url.startswith(("http://", "https://")):
        return canonical_url(legacy_url), legacy_url
    if link.startswith(("http://", "https://")):
        meta["legacy_url"] = legacy_url
        return canonical_url(link), link
    return None, legacy_url  # open-dataset rows (hf://, dataset://): locator is the legacy id


def build_record(source, row, db_name, date_patches=None):
    """
    `source`: entry from manifests/sources.yaml; `row`: dict of an `articles` row.
    `date_patches`: PatchStore.load() map for this source; a patch only fills a
    missing date and never one later than the crawl.
    Returns (record | None, skip_reason | None). Records still need schema validation.
    """
    source_id = source["id"]
    meta = _parse_json(row.get("metadata"))
    meta = dict(meta) if isinstance(meta, dict) else ({"raw_metadata": meta} if meta else {})
    for key in [k for k in meta if k.lower() in SENSITIVE_META_KEYS]:
        meta.pop(key)
    url, locator = _locate(row, meta)
    doc_id = make_doc_id(source_id, locator)
    meta.update(legacy_db=db_name, legacy_id=row.get("id"))

    text = _strip_boilerplate(source_id, _nfc(row.get("content") or ""))
    text = text.strip()
    if not text:
        return None, "empty_content"
    usage_scope = source["usage_scope"]
    if broken_diacritic_lines(text) >= BROKEN_DIACRITICS_MIN_LINES:
        meta["text_issue"] = "broken_diacritics"
        usage_scope = "blocked"  # kept visible for re-extraction tools, never trained on

    crawl_time = datetime.fromisoformat(row["created_at"]) if row.get("created_at") else None
    title = _nfc(row.get("title"))
    published, date_tag = resolve_publish_date(source_id, meta, url or locator, title, text[:1500], crawl_time)
    date_patch = (date_patches or {}).get(doc_id)
    if published is None and date_patch and date_patch["publish_date"] is not None:
        if crawl_time is None or date_patch["publish_date"] <= crawl_time.date():
            published, date_tag = date_patch["publish_date"], date_patch["tag"]

    author = row.get("author")
    record = {
        "doc_id": doc_id,
        "source_id": source_id,
        "source_type": source["source_type"],
        "url": url,
        "title": title,
        "text": text,
        "lang": None,
        "lang_score": None,
        "publish_date": published,
        "publish_date_source": date_tag,
        "crawl_time": crawl_time,
        "era": era_for(published, crawl_time),
        "author": _nfc(author if isinstance(author, str) else json.dumps(author, ensure_ascii=False)) if author else None,
        "category": _nfc(row.get("category")),
        "tags": _tags(row.get("tags")),
        "raw_ref": None,
        "text_sha256": text_sha256(text),
        "license": source["license"],
        "usage_scope": usage_scope,
        "extractor": EXTRACTOR,
        "meta": json.dumps(meta, ensure_ascii=False, default=str),
    }
    return record, None
