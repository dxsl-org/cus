"""
Sites without a complete sitemap: walk HTML listing pages (date archives, categories,
pagination) and collect article links.

params:
  start_urls: [url, ...]                        listing entry points (optional)
  month_archive: {pattern: "https://x.vn/{yyyy}/{mm}/", from: "2008-01"}
                                                 one entry point per month up to now (optional)
  follow: regex                                  further listing pages (pagination) to walk
  include: regex                                 article URLs

Example: WordPress.com keeps only the newest 1000 posts in sitemap.xml and blocks its
REST API in robots.txt, but /YYYY/MM/page/N/ archives list every post.
"""
import re
from datetime import date
from urllib.parse import urljoin

from selectolax.lexbor import LexborHTMLParser

from crawler.adapters import sitemap
from crawler.adapters.base import Link

DOCUMENT_KINDS = ("article",)
extract = sitemap.extract  # article pages are extracted the same way


def _months(start, end):
    year, month = map(int, start.split("-"))
    while (year, month) <= (end.year, end.month):
        yield year, month
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)


def seeds(spec):
    p = spec["params"]
    urls = list(p.get("start_urls", []))
    if archive := p.get("month_archive"):
        urls += [archive["pattern"].format(yyyy=y, mm=f"{m:02d}")
                 for y, m in _months(archive["from"], date.today())]
    return [Link(u, "listing", priority=5) for u in urls]


def discover(spec, page):
    if page.kind != "listing":
        return []
    follow, include = re.compile(spec["params"]["follow"]), re.compile(spec["params"]["include"])
    links = {}
    for a in LexborHTMLParser(page.text()).css("a[href]"):
        url = urljoin(page.url, a.attributes["href"]).split("#")[0]
        if include.search(url):
            links[url] = Link(url, "article")
        elif follow.search(url):
            links[url] = Link(url, "listing", priority=5)
    return list(links.values())
