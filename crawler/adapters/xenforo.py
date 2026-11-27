"""
XenForo 2 forums: every node, every listing page, every thread, every thread page.

params:
  base: https://forum.example.vn          site root
  forums: ["/forums/"]                    entry pages (forum index or specific nodes)
  thread_route: "threads"                 URL segment of threads ("t" on voz.vn)
  forum_route: "forums"                   URL segment of nodes ("f" on voz.vn)

Kinds: forum (node/listing pages) → thread (one per thread page; group_key = thread
URL, seq = page number). A thread becomes ONE document: all posts of all pages in
order, quoted replies removed (they duplicate earlier posts). Usernames are not kept.
"""
import re
from datetime import datetime
from urllib.parse import urljoin

from selectolax.lexbor import LexborHTMLParser

from crawler.adapters.base import Doc, Link
from crawler.html_text import fragment_to_text
from pipeline.ingest.html_dates import iso_date

DOCUMENT_KINDS = ("thread",)
_POST_SEPARATOR = "\n\n---\n\n"


def _routes(spec):
    p = spec["params"]
    thread, forum = re.escape(p.get("thread_route", "threads")), re.escape(p.get("forum_route", "forums"))
    return re.compile(rf"(/{thread}/[^/?#]+\.\d+)"), re.compile(rf"/{forum}/[^/?#]+\.\d+/?(page-\d+)?$")


def _thread_root(url, thread_re):
    m = thread_re.search(url)
    return url[: m.end()] + "/" if m else None


def seeds(spec):
    base = spec["params"]["base"]
    return [Link(urljoin(base, path), "forum", priority=10) for path in spec["params"]["forums"]]


def discover(spec, page):
    tree = LexborHTMLParser(page.text())
    base = spec["params"]["base"]
    thread_re, forum_re = _routes(spec)
    links = []
    if page.kind == "forum":
        for a in tree.css("a[href]"):
            href = urljoin(base, a.attributes["href"])
            if root := _thread_root(href, thread_re):
                links.append(Link(root, "thread", priority=1, group_key=root, seq=1))
            elif forum_re.search(href.split("?")[0]) and href.startswith(base):
                links.append(Link(href.split("?")[0], "forum", priority=5))
        return links
    if page.kind == "thread" and page.seq == 1:
        pages = [int(m) for a in tree.css(".pageNav-main a[href]")
                 for m in re.findall(r"/page-(\d+)", a.attributes["href"])]
        root = _thread_root(page.url, thread_re)
        return [Link(f"{root}page-{n}", "thread", priority=2, group_key=root, seq=n)
                for n in range(2, max(pages, default=1) + 1)]
    return []


def _posts(html):
    tree = LexborHTMLParser(html)
    for article in tree.css("article.message"):
        body = article.css_first(".message-body .bbWrapper")
        if body is None:
            continue
        when = article.css_first("time.u-dt")
        yield fragment_to_text(body.html, drop_selectors=(".bbCodeBlock--quote", "blockquote")), \
            when.attributes.get("datetime") if when else None


def extract(spec, group_key, pages):
    first = LexborHTMLParser(pages[0].text())
    title_node = first.css_first("h1.p-title-value")
    posts, first_time = [], None
    for page in pages:
        for text, when in _posts(page.text()):
            first_time = first_time or when
            if text:
                posts.append(text)
    if not posts:
        return []
    bound = (pages[0].captured_at or pages[0].fetched_at or datetime.now()).date()
    published = iso_date(first_time, bound)
    breadcrumb = [n.text(strip=True) for n in first.css(".p-breadcrumbs a span")]
    return [Doc(url=group_key, text=_POST_SEPARATOR.join(posts),
                title=title_node.text(strip=True) if title_node else None, publish_date=published,
                publish_date_source="meta:first_post_time" if published else "none",
                category=breadcrumb[-1] if breadcrumb else None,
                meta={"posts": len(posts), "pages": len(pages)})]
