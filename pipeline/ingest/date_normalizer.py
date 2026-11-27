"""
Normalize heterogeneous publish dates from crawled Vietnamese sources to ISO dates.

Publish date is the key that lets the corpus be split into pre/post-LLM-era text
(ChatGPT launched 2022-11-30). `created_at` in the DB is crawl/migration time: it is
only an upper bound for the publish date, never the publish date itself.
"""
import re
import unicodedata
from datetime import date, datetime

MIN_DATE = date(1945, 1, 1)

# Keys observed across sources, in priority order.
METADATA_DATE_KEYS = (
    "publish_date", "published_date", "published_at", "published_time", "date", "created_at",
)

# Scraped fields proven to hold the crawl date, not the publish date (corpus report
# 2026-11-13: all docs on 2-5 days). baovanhoa scraped the site-header "today" date.
UNRELIABLE_META_KEYS = {"tuoitre_vn": frozenset({"date"}), "baovanhoa_vn": frozenset({"published_date"})}

# Sources whose title/body header carries the document's own issue date
# (court rulings, legal documents). For news/wiki, dates in text refer to events.
TEXT_DATE_SOURCES = frozenset({"toaan_gov_vn", "vbpl_vn", "vbpq_org_vn", "thuvienphapluat_vn"})

# Longest names first so "mười một" wins over "mười".
_VI_MONTHS = {
    "mười hai": 12, "mười một": 11, "mười": 10, "chạp": 12, "giêng": 1,
    "một": 1, "hai": 2, "ba": 3, "tư": 4, "bốn": 4, "năm": 5,
    "sáu": 6, "bảy": 7, "tám": 8, "chín": 9,
}
_VI_MONTH_ALT = "|".join(re.escape(k) for k in _VI_MONTHS)

_ISO = re.compile(r"(\d{4})-(\d{2})-(\d{2})")
_VI_LONG_NUM = re.compile(r"ngày\s+(\d{1,2})\s+tháng\s+(\d{1,2})\s*,?\s*năm\s+(\d{4})", re.I)
_VI_WORD_MONTH = re.compile(rf"(\d{{1,2}})\s+tháng\s+({_VI_MONTH_ALT})\s*,?\s*(\d{{4}})", re.I)
_VI_NUM_MONTH = re.compile(r"(\d{1,2})\s+tháng\s+(\d{1,2})\s*,?\s*(\d{4})", re.I)
# No trailing \b: some sources glue junk to the year ("20140", "2026 09:21Chia sẻ").
_DMY4 = re.compile(r"(\d{1,2})\s*[/.\-–]\s*(\d{1,2})\s*[/.\-–]\s*(\d{4})")
_DMY2 = re.compile(r"(?<!\d)(\d{1,2})/(\d{1,2})/(\d{2})(?!\d)")

# Dates assigned by the CMS at publication; more trustworthy than scraped fields
# (tuoitre's migrated `date` field holds the crawl date, its URL id holds the real one).
_URL_PATTERNS = (
    re.compile(r"https?://[^?#]*/(\d{4})/(\d{2})/(\d{2})/"),          # WordPress permalinks
    re.compile(r"https?://[^?#]*-(20\d{2})(\d{2})(\d{2})\d{8,}\.htm"),  # tuoitre-family ids (16-17 digits)
)


def _make(y, m, d, not_after):
    try:
        value = date(int(y), int(m), int(d))
    except ValueError:
        return None
    return value if MIN_DATE <= value <= not_after else None


def parse_vi_date(text, not_after=None):
    """
    Parse the first recognizable date in `text`.

    Handles ISO, dd/mm/yyyy (with weekday/time noise), dd/mm/yy (XenForo),
    "8 Tháng tám 2021", and legal "ngày 5 tháng 4 năm 2018".
    Dates outside [MIN_DATE, not_after] (default: today) are rejected.
    Returns `datetime.date` or None.
    """
    if not text:
        return None
    not_after = not_after or date.today()
    text = unicodedata.normalize("NFC", str(text))
    if m := _ISO.search(text):
        return _make(*m.groups(), not_after)
    if m := _VI_LONG_NUM.search(text):
        return _make(m[3], m[2], m[1], not_after)
    if m := _VI_WORD_MONTH.search(text):
        return _make(m[3], _VI_MONTHS[m[2].lower()], m[1], not_after)
    if m := _VI_NUM_MONTH.search(text):
        return _make(m[3], m[2], m[1], not_after)
    if m := _DMY4.search(text):
        return _make(m[3], m[2], m[1], not_after)
    if m := _DMY2.search(text):
        return _make(2000 + int(m[3]), m[2], m[1], not_after)
    return None


def date_from_url(url, not_after=None):
    """Extract a CMS-assigned publish date from an article URL, else None."""
    if not url:
        return None
    not_after = not_after or date.today()
    for pattern in _URL_PATTERNS:
        if m := pattern.search(url):
            return _make(*m.groups(), not_after)
    return None


def _as_date(value):
    if isinstance(value, datetime):  # check first: datetime is a subclass of date
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return datetime.fromisoformat(str(value)[:19]).date()
    except (TypeError, ValueError):
        return None


def resolve_publish_date(source_id, metadata, url=None, title=None, content_head=None, crawled_at=None):
    """
    Best-effort publish date for one document.

    Precedence: URL/link (CMS-assigned) > metadata keys > title > content header.
    Title/content are only consulted for TEXT_DATE_SOURCES. `crawled_at` (the DB
    `created_at`) is an upper bound: any candidate after it is rejected.
    Returns (date | None, source_tag); tag is "url", "meta:<key>", "title",
    "content", or "none".
    """
    not_after = _as_date(crawled_at) or date.today()
    metadata = metadata if isinstance(metadata, dict) else {}
    for candidate in (metadata.get("link"), url):
        if value := date_from_url(candidate, not_after):
            return value, "url"
    unreliable = UNRELIABLE_META_KEYS.get(source_id, frozenset())
    for key in (k for k in METADATA_DATE_KEYS if k not in unreliable):
        if value := parse_vi_date(metadata.get(key), not_after):
            return value, f"meta:{key}"
    if source_id in TEXT_DATE_SOURCES:
        if value := parse_vi_date(title, not_after):
            return value, "title"
        if value := parse_vi_date(content_head, not_after):
            return value, "content"
    return None, "none"
