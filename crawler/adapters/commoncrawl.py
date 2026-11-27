"""
Historical pages of a site from Common Crawl, without touching the site itself.

params:
  url_pattern: "voz.vn/t/*"
  crawls: ["CC-MAIN-2022-49", "CC-MAIN-2022-40", ...]   (index ids)
  include: regex   (optional)

Index query (paged) → each capture is fetched as a byte range of the CC WARC file.
The range is carried in the URL fragment ("#bytes=start-end") and turned into a
Range header by the fetcher. crawl_time = CC capture time.
"""
import gzip
import io
import json
import re
from datetime import datetime
from urllib.parse import urlencode

from warcio.archiveiterator import ArchiveIterator

from crawler.adapters.base import Doc, Link
from crawler.html_text import article_title, article_to_text
from pipeline.ingest.html_dates import extract_publish_date

DOCUMENT_KINDS = ("cc_record",)
# Common Crawl's robots.txt disallows crawlers, but its data is published for download
# ("of course you are welcome to download our data via https" — data.commoncrawl.org/robots.txt).
ROBOTS_EXEMPT_HOSTS = ("index.commoncrawl.org", "data.commoncrawl.org")
INDEX = "https://index.commoncrawl.org/{crawl}-index?"
DATA = "https://data.commoncrawl.org/"


def _index_url(crawl, pattern, page_no=None, num_pages=False):
    # No server-side filters: the CDX server answers 404 when a filter leaves a page empty,
    # which is indistinguishable from an error. Status/mime are filtered in discover().
    query = {"url": pattern, "output": "json"}
    if num_pages:
        query["showNumPages"] = "true"
    if page_no is not None:
        query["page"] = page_no
    return INDEX.format(crawl=crawl) + urlencode(query, doseq=True)


def seeds(spec):
    p = spec["params"]
    return [Link(_index_url(crawl, p["url_pattern"], num_pages=True), "cc_pages", priority=10, group_key=crawl)
            for crawl in p["crawls"]]


def discover(spec, page):
    p = spec["params"]
    if page.kind == "cc_pages":
        crawl = re.search(r"/(CC-MAIN-[\d-]+)-index", page.url)[1]
        pages = json.loads(page.body).get("pages", 0)
        return [Link(_index_url(crawl, p["url_pattern"], n), "cc_index", priority=9) for n in range(pages)]
    if page.kind != "cc_index":
        return []
    include = re.compile(p["include"]) if p.get("include") else None
    links, records, corrupt = [], [], 0
    for line in page.body.decode("utf-8", errors="replace").splitlines():
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:  # the overloaded index server sometimes mangles single lines
            corrupt += 1
    if corrupt and not records:
        raise ValueError(f"index page unreadable ({corrupt} corrupt lines)")  # → requeued and refetched
    for rec in records:
        if rec.get("status") != "200" or "html" not in rec.get("mime", ""):
            continue
        if include and not include.search(rec["url"]):
            continue
        start, length = int(rec["offset"]), int(rec["length"])
        captured = datetime.strptime(rec["timestamp"], "%Y%m%d%H%M%S").isoformat()
        links.append(Link(f"{DATA}{rec['filename']}#bytes={start}-{start + length - 1}", "cc_record",
                          group_key=rec["url"], captured_at=captured))
    return links


def _payload(record_bytes):
    """HTML payload of one gzipped CC WARC response record."""
    stream = io.BytesIO(gzip.decompress(record_bytes) if record_bytes[:2] == b"\x1f\x8b" else record_bytes)
    for record in ArchiveIterator(stream):
        if record.rec_type == "response":
            return record.content_stream().read().decode("utf-8", errors="replace")
    return ""


def extract(spec, group_key, pages):
    page = pages[-1]  # several crawls may have captured the URL; keep the latest pre-cutoff one fetched
    html = _payload(page.body)
    text = article_to_text(html, group_key) if html else ""
    if not text:
        return []
    published, field = extract_publish_date(html, page.captured_at.date())
    return [Doc(url=group_key, text=text, title=article_title(html), publish_date=published,
                publish_date_source="meta:" + field if published else "none",
                meta={"archive": "commoncrawl", "capture": page.url})]
