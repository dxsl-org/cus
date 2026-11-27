"""
DocumentRecord: the one canonical shape every pretraining source is converted to.

Contract (see docs/data-contract.md):
- `text`/`title` are Unicode NFC.
- `publish_date` is never derived from crawl time; it must be <= `crawl_time`.
- `era` is a pure function of `publish_date` (cutoff = ChatGPT public launch).
- `doc_id` is stable across re-crawls: derived from source + canonical locator only.
"""
import hashlib
import json
import unicodedata
from datetime import date, datetime
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import pyarrow as pa

LLM_CUTOFF = date(2022, 12, 1)  # ChatGPT launched 2022-11-30; earlier text is presumed human-written.

SOURCE_TYPES = frozenset({
    "news", "blog", "forum", "qa", "legal", "court", "academic", "education",
    "reference", "corporate", "social", "book", "code", "open_dataset",
})
ERAS = frozenset({"pre_llm", "post_llm", "unknown"})
USAGE_SCOPES = frozenset({"train_ok", "research_only", "blocked"})
PUBLISH_DATE_SOURCES = frozenset({"url", "title", "content", "interp", "recrawl", "dump", "none"})
SENSITIVE_META_KEYS = frozenset({"cookie", "cookies", "authorization", "token", "access_token", "api_key", "password"})

# Only params known to be pure tracking; site params like `ref` can identify content.
_TRACKING_PARAMS = frozenset({"fbclid", "gclid", "zarsrc", "zarsrc_id"})

ARROW_SCHEMA = pa.schema([
    ("doc_id", pa.string()),
    ("source_id", pa.string()),
    ("source_type", pa.string()),
    ("url", pa.string()),
    ("title", pa.string()),
    ("text", pa.large_string()),
    ("lang", pa.string()),
    ("lang_score", pa.float32()),
    ("publish_date", pa.date32()),
    ("publish_date_source", pa.string()),
    ("crawl_time", pa.timestamp("ms")),  # Parquet has no second unit; "s" would read back as "ms".
    ("era", pa.string()),
    ("author", pa.string()),
    ("category", pa.string()),
    ("tags", pa.list_(pa.string())),
    ("raw_ref", pa.string()),
    ("text_sha256", pa.string()),
    ("license", pa.string()),
    ("usage_scope", pa.string()),
    ("extractor", pa.string()),
    ("meta", pa.string()),
])
REQUIRED_FIELDS = ("doc_id", "source_id", "source_type", "text", "publish_date_source",
                   "crawl_time", "era", "text_sha256", "license", "usage_scope", "extractor")


def canonical_url(url):
    """Lowercase scheme/host, drop fragment and tracking params (utm_*, fbclid, ...); keep the rest."""
    parts = urlsplit(url.strip())
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
             if not k.lower().startswith("utm_") and k.lower() not in _TRACKING_PARAMS]
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path, urlencode(query), ""))


def make_doc_id(source_id, locator):
    """
    Stable id from source + locator (canonical URL, or file path/sha for books).
    URLs are canonicalized first so tracking variants map to the same id.
    """
    if locator.startswith(("http://", "https://")):
        locator = canonical_url(locator)
    return hashlib.sha1(f"{source_id}\x00{locator}".encode("utf-8")).hexdigest()[:20]


def era_for(publish_date, crawl_time=None):
    """
    pre_llm if published before the cutoff, or (date unknown) fetched before it:
    a page captured before ChatGPT existed was necessarily written before it.
    """
    if publish_date is not None:
        return "pre_llm" if publish_date < LLM_CUTOFF else "post_llm"
    crawled = crawl_time.date() if isinstance(crawl_time, datetime) else crawl_time
    return "pre_llm" if crawled is not None and crawled < LLM_CUTOFF else "unknown"


def text_sha256(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _is_nfc(value):
    return value is None or unicodedata.is_normalized("NFC", value)


def _publish_source_ok(tag):
    return tag in PUBLISH_DATE_SOURCES or (isinstance(tag, str) and tag.startswith("meta:"))


def validate(record):
    """
    Check one record against the contract.
    Returns a list of human-readable violations; empty list means valid.
    """
    errors = [f"missing {f}" for f in REQUIRED_FIELDS if record.get(f) in (None, "")]
    if errors:
        return errors
    if record["source_type"] not in SOURCE_TYPES:
        errors.append(f"source_type {record['source_type']!r} not in SOURCE_TYPES")
    if record["usage_scope"] not in USAGE_SCOPES:
        errors.append(f"usage_scope {record['usage_scope']!r} not in USAGE_SCOPES")
    if not _publish_source_ok(record["publish_date_source"]):
        errors.append(f"publish_date_source {record['publish_date_source']!r} invalid")
    for field in ("text", "title"):
        if not _is_nfc(record.get(field)):
            errors.append(f"{field} is not NFC")
    if record["text_sha256"] != text_sha256(record["text"]):
        errors.append("text_sha256 does not match text")

    published, crawled = record.get("publish_date"), record["crawl_time"]
    crawled_day = crawled.date() if isinstance(crawled, datetime) else crawled
    if published is not None and published > crawled_day:
        errors.append(f"publish_date {published} after crawl_time {crawled_day}")
    if (published is None) != (record["publish_date_source"] == "none"):
        errors.append("publish_date and publish_date_source='none' disagree")
    expected_era = era_for(published, crawled)
    if record["era"] != expected_era:
        errors.append(f"era {record['era']!r} != {expected_era!r} for publish_date {published}, crawl_time {crawled}")

    if record.get("meta"):
        try:
            meta = json.loads(record["meta"])
        except json.JSONDecodeError:
            errors.append("meta is not valid JSON")
        else:
            leaked = SENSITIVE_META_KEYS & {k.lower() for k in meta} if isinstance(meta, dict) else set()
            if leaked:
                errors.append(f"meta contains sensitive keys {sorted(leaked)}")
    return errors
