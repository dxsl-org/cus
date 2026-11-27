"""
tinhte.vn (Next.js front end over a XenForo/bdApi backend).

Why a dedicated adapter: threads promoted to the homepage are often machine-translated
or AI-written, while user threads and comments are genuinely human. Each thread is
split into two documents so they can be weighted separately:
  - the opening post, meta.content_role = "editorial" (promoted, or creator is mod /
    verified writer) or "user_op";
  - all replies, meta.content_role = "user_reply" (always human discussion).

Data sources (all allowed by robots.txt):
  - sitemap.xml → every /thread/ URL;
  - each thread page embeds the API response in __NEXT_DATA__ (posts, creator flags,
    page count); further pages are /thread/<slug>.<id>/page-N/;
  - the homepage feed "threads/promoted" (≈128k threads) is paged through the public
    one-time token the homepage itself uses; tokens expire, so a fresh homepage is
    fetched every TOKEN_PAGES pages.

params.min_thread_id: threads older than ~mid-2016 (id < ≈2.6M, measured 2026-11-14)
answer "Bạn cần đăng nhập…" instead of posts. We do not crawl behind logins, so those
ids are not enqueued; their public historical copies come from Common Crawl/Wayback.
"""
import json
import re
from datetime import datetime
from urllib.parse import parse_qs, urlsplit

from selectolax.lexbor import LexborHTMLParser

from crawler.adapters import sitemap
from crawler.adapters.base import Doc, Link
from crawler.html_text import fragment_to_text

DOCUMENT_KINDS = ("thread",)
CONTEXT_KINDS = ("promoted",)
HOME = "https://tinhte.vn/"
PROMOTED = "https://tinhte.vn/appforo/index.php?threads/promoted&limit=20&page={page}&oauth_token={token}"
TOKEN_PAGES = 50  # refresh the homepage token this often (tokens expire within hours)
_THREAD = re.compile(r"^(https://tinhte\.vn/thread/[^/?#]+\.(\d+))/")
_QUOTES = ("blockquote", ".bbCodeQuote", ".bbCodeBlock--quote")
_SEPARATOR = "\n\n---\n\n"


def seeds(spec):
    return [Link(u, "sitemap", priority=10) for u in spec["params"]["sitemaps"]] + [Link(HOME + "?feed_page=1", "home", priority=8)]


def _next_data(html):
    node = LexborHTMLParser(html).css_first("script#__NEXT_DATA__")
    return json.loads(node.text()) if node else {}


def _thread_job(html):
    jobs = _next_data(html).get("props", {}).get("pageProps", {}).get("apiData", {}).get("jobs", {})
    return next((j for j in jobs.values() if isinstance(j, dict) and "posts" in j and "thread" in j), None)


def thread_root(url):
    m = _THREAD.match(url)
    return (m[1] + "/", int(m[2])) if m else (None, None)


def discover(spec, page):
    if page.kind == "sitemap":
        links = []
        for link in sitemap.discover({"params": {"include": r"^https://tinhte\.vn/thread/"}}, page):
            if link.kind == "sitemap":
                links.append(link)
            elif (root := thread_root(link.url))[0] and root[1] >= spec["params"].get("min_thread_id", 0):
                links.append(Link(root[0], "thread", priority=1, group_key=root[0], seq=1))
        return links
    if page.kind == "home":
        token = _next_data(page.text())["props"]["pageProps"]["apiConfig"]["ott"]
        start = int(parse_qs(urlsplit(page.url).query).get("feed_page", ["1"])[0])
        return [Link(PROMOTED.format(page=start, token=token), "promoted", priority=7, seq=start)]
    if page.kind == "promoted":
        data = json.loads(page.body)
        query = parse_qs(urlsplit(page.url).query)
        n, limit = int(query["page"][0]), int(query["limit"][0])
        if not data.get("threads") or n * limit >= int(data.get("threads_total", 0)):
            return []
        if n % TOKEN_PAGES == 0:
            return [Link(HOME + f"?feed_page={n + 1}", "home", priority=8)]
        return [Link(PROMOTED.format(page=n + 1, token=query["oauth_token"][0]), "promoted", priority=7, seq=n + 1)]
    if page.kind == "thread" and page.seq == 1:
        job = _thread_job(page.text())
        pages = int((job or {}).get("links", {}).get("pages", 1) or 1)
        root = thread_root(page.url)[0]
        return [Link(f"{root}page-{n}/", "thread", priority=2, group_key=root, seq=n) for n in range(2, pages + 1)]
    return []


def build_context(spec, groups):
    """Promoted (homepage) thread ids from every fetched feed page."""
    promoted = set()
    for _, pages in groups:
        for page in pages:
            promoted.update(int(t["thread_id"]) for t in json.loads(page.body).get("threads", []))
    return {"promoted": promoted}


def _post_text(post):
    html = post.get("post_body_html") or ""
    return fragment_to_text(html, drop_selectors=_QUOTES) if html else (post.get("post_body_plain_text") or "").strip()


def _day(timestamp):
    return datetime.fromtimestamp(int(timestamp)).date() if timestamp else None


def extract(spec, group_key, pages):
    jobs = [j for j in (_thread_job(p.text()) for p in pages) if j]
    if not jobs:
        return []  # deleted/hidden thread: the posts API job failed
    thread = jobs[0]["thread"]
    posts = {p["post_id"]: p for j in jobs for p in j["posts"]}.values()  # nested replies can repeat
    posts = sorted(posts, key=lambda p: int(p.get("post_create_date") or 0))
    first = next((p for p in posts if str(p.get("post_is_first_post")).lower() == "true"), None)
    replies = [p for p in posts if p is not first and str(p.get("post_is_deleted")).lower() != "true"]
    thread_id = int(thread["thread_id"])
    promoted = thread_id in spec.get("context", {}).get("promoted", set())
    staff = str(thread.get("creator_is_mod")).lower() == "true" or str(thread.get("creator_has_verified_badge")).lower() == "true"
    meta = {"thread_id": thread_id, "forum_id": thread.get("forum_id"), "promoted": promoted, "creator_staff": staff,
            "tags": list((thread.get("thread_tags") or {}).values()) if isinstance(thread.get("thread_tags"), dict) else []}
    title = thread.get("thread_title")
    docs = []
    if first and (text := _post_text(first)):
        role = "editorial" if promoted or staff else "user_op"
        day = _day(first.get("post_create_date"))
        docs.append(Doc(url=group_key, text=text, title=title, publish_date=day,
                        publish_date_source="meta:post_create_date" if day else "none",
                        meta={**meta, "content_role": role}, locator=f"thread:{thread_id}:op"))
    reply_texts = [t for t in (_post_text(p) for p in replies) if t]
    if reply_texts:
        day = _day(replies[0].get("post_create_date"))
        docs.append(Doc(url=group_key, text=_SEPARATOR.join(reply_texts), title=title, publish_date=day,
                        publish_date_source="meta:post_create_date" if day else "none",
                        meta={**meta, "content_role": "user_reply", "replies": len(reply_texts)},
                        locator=f"thread:{thread_id}:replies"))
    return docs
