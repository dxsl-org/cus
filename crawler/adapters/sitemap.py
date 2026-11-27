"""
News/article sites via XML sitemaps.

params:
  sitemaps: [url, ...]      sitemap index or urlset URLs (gzip supported)
  include: regex            article URLs must match (optional)
  exclude: regex            article URLs to skip (optional)

Unlike the legacy plugin (which read only the last 2 sub-sitemaps), every
sub-sitemap is followed, so the whole archive is enumerated.
"""
import gzip
import re
from datetime import datetime

from lxml import etree

from crawler.adapters.base import Doc, Link
from crawler.html_text import article_title, article_to_text
from pipeline.ingest.html_dates import extract_publish_date

DOCUMENT_KINDS = ("article",)
_LOC = "{*}loc"


def seeds(spec):
    return [Link(url, "sitemap", priority=10) for url in spec["params"]["sitemaps"]]


def _parse_sitemap(body):
    if body[:2] == b"\x1f\x8b":
        body = gzip.decompress(body)
    root = etree.fromstring(body, parser=etree.XMLParser(recover=True, huge_tree=True))
    if root is None:
        return "", []
    kind = etree.QName(root).localname  # sitemapindex | urlset | rss
    return kind, [el.text.strip() for el in root.iter(_LOC, "link") if el.text and el.text.strip()]


def discover(spec, page):
    if page.kind != "sitemap":
        return []
    kind, locs = _parse_sitemap(page.body)
    if kind == "sitemapindex":
        return [Link(u, "sitemap", priority=10) for u in locs]
    include = re.compile(spec["params"]["include"]) if spec["params"].get("include") else None
    exclude = re.compile(spec["params"]["exclude"]) if spec["params"].get("exclude") else None
    return [Link(u, "article") for u in locs
            if (include is None or include.search(u)) and not (exclude and exclude.search(u))]


def extract(spec, group_key, pages):
    page = pages[0]
    html = page.text()
    text = article_to_text(html, page.url)
    if not text:
        return []
    bound = (page.captured_at or page.fetched_at or datetime.now()).date()
    published, field = extract_publish_date(html, bound)
    return [Doc(url=page.url, text=text, title=article_title(html), publish_date=published,
                publish_date_source="meta:" + field if published else "none")]
