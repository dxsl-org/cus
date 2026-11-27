"""Publish date from machine-readable page metadata (meta tags, JSON-LD, <time>)."""
import re
from datetime import date

from selectolax.lexbor import LexborHTMLParser

_ISO_DATE = re.compile(r"(\d{4})-(\d{2})-(\d{2})")
_LD_PUBLISHED = re.compile(r'"datePublished"\s*:\s*"([^"]+)"')
# ISO-only on purpose: some sites also emit US-style "3/1/2026 6:39 AM" which is ambiguous.
_META_KEYS = ("article:published_time", "datePublished", "og:published_time", "pubdate", "publishdate")


def iso_date(value, not_after):
    """First ISO yyyy-mm-dd in `value` within [1945-01-01, not_after], else None."""
    m = _ISO_DATE.search(value or "")
    if not m:
        return None
    try:
        found = date(int(m[1]), int(m[2]), int(m[3]))
    except ValueError:
        return None
    return found if date(1945, 1, 1) <= found <= not_after else None


def extract_publish_date(html, not_after):
    """Return (date, field) from page metadata, or (None, reason). Only ISO dates are trusted."""
    tree = LexborHTMLParser(html)
    metas = {}
    for node in tree.css("meta"):
        attrs = node.attributes
        key = attrs.get("property") or attrs.get("itemprop") or attrs.get("name")
        if key and key not in metas:
            metas[key] = attrs.get("content")
    for key in _META_KEYS:
        if found := iso_date(metas.get(key), not_after):
            return found, f"meta:{key}"
    for script in tree.css('script[type="application/ld+json"]'):
        for value in _LD_PUBLISHED.findall(script.text() or ""):
            if found := iso_date(value, not_after):
                return found, "ld:datePublished"
    for node in tree.css("time[datetime]"):
        if found := iso_date(node.attributes.get("datetime"), not_after):
            return found, "time[datetime]"
    return None, "no_date_meta"
