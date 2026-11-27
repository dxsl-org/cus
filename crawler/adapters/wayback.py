"""
Dead or changed sites via the Internet Archive (Wayback Machine).

params:
  url_patterns: ["anh2.com/*", "anh2.net/*"]   CDX url matches (prefix with /*); one CDX listing each
  from: "2005"   to: "20221130"          capture window; default ends before the LLM cutoff
  include: regex                         original URLs to keep (optional)

CDX listing (collapsed to one capture per URL) → fetch each capture raw via the
"id_" URL form (original bytes, no Wayback toolbar). crawl_time of the resulting
document = capture time, so pre-2022 captures are pre_llm even without a publish date.
"""
import json
import re
from datetime import datetime
from urllib.parse import parse_qs, quote, urlencode, urlsplit

from crawler.adapters.base import Doc, Link
from crawler.html_text import article_title, article_to_text
from pipeline.ingest.html_dates import extract_publish_date

DOCUMENT_KINDS = ("capture",)
CDX = "https://web.archive.org/cdx/search/cdx?"
PAGE_ROWS = 5000


def _cdx_url(spec, pattern, resume_key=None):
    p = spec["params"]
    query = {"url": pattern, "output": "json", "fl": "timestamp,original",
             "filter": ["statuscode:200", "mimetype:text/html"], "collapse": "urlkey",
             "from": p.get("from", "1996"), "to": p.get("to", "20221130"), "limit": PAGE_ROWS,
             "showResumeKey": "true"}
    if resume_key:
        query["resumeKey"] = resume_key
    return CDX + urlencode(query, doseq=True)


def seeds(spec):
    return [Link(_cdx_url(spec, pattern), "cdx", priority=10) for pattern in spec["params"]["url_patterns"]]


def _iso(timestamp):
    return datetime.strptime(timestamp[:14], "%Y%m%d%H%M%S").isoformat()


def discover(spec, page):
    if page.kind != "cdx":
        return []
    rows = json.loads(page.body or b"[]")
    include = re.compile(spec["params"]["include"]) if spec["params"].get("include") else None
    links, resume_key = [], None
    for row in rows[1:]:  # first row is the field header
        if len(row) == 1:
            resume_key = row[0]
        elif len(row) == 2 and (include is None or include.search(row[1])):
            timestamp, original = row
            links.append(Link(f"https://web.archive.org/web/{timestamp}id_/{quote(original, safe=':/?&=%#')}",
                              "capture", group_key=original, captured_at=_iso(timestamp)))
    if resume_key:
        pattern = parse_qs(urlsplit(page.url).query)["url"][0]
        links.append(Link(_cdx_url(spec, pattern, resume_key), "cdx", priority=10))
    return links


def extract(spec, group_key, pages):
    page = pages[0]
    html = page.text()
    text = article_to_text(html, group_key)
    if not text:
        return []
    published, field = extract_publish_date(html, page.captured_at.date())
    return [Doc(url=group_key, text=text, title=article_title(html), publish_date=published,
                publish_date_source="meta:" + field if published else "none",
                meta={"archive": "wayback", "capture": page.url})]
