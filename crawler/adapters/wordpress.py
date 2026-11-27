"""
WordPress sites via the REST API (no HTML scraping needed).

params:
  site: https://example.com            self-hosted: <site>/wp-json/wp/v2/posts
  wpcom: example.wordpress.com          wordpress.com: public-api.wordpress.com/wp/v2/sites/<wpcom>/posts
  per_page: 100

Page 1 reports X-WP-TotalPages, so all listing pages are enqueued at once.
"""
import html
import json
from datetime import datetime

from crawler.adapters.base import Doc, Link
from crawler.html_text import fragment_to_text

DOCUMENT_KINDS = ("wp_posts",)


def _api(spec):
    p = spec["params"]
    base = (f"https://public-api.wordpress.com/wp/v2/sites/{p['wpcom']}/posts" if p.get("wpcom")
            else f"{p['site'].rstrip('/')}/wp-json/wp/v2/posts")
    return f"{base}?per_page={p.get('per_page', 100)}&orderby=date&order=asc&page="


def seeds(spec):
    return [Link(_api(spec) + "1", "wp_posts", priority=10, seq=1)]


def discover(spec, page):
    if page.seq != 1:
        return []
    total = next((v for k, v in page.headers.items() if k.lower() == "x-wp-totalpages"), "1")
    return [Link(_api(spec) + str(n), "wp_posts", priority=5, seq=n) for n in range(2, int(total) + 1)]


def extract(spec, group_key, pages):
    docs = []
    for post in json.loads(pages[0].body):
        text = fragment_to_text(post.get("content", {}).get("rendered", ""))
        if not text:
            continue
        published = datetime.fromisoformat(post["date_gmt"]).date() if post.get("date_gmt") else None
        docs.append(Doc(url=post["link"], text=text, title=html.unescape(post["title"]["rendered"]).strip() or None,
                        publish_date=published, publish_date_source="meta:date_gmt" if published else "none",
                        meta={"wp_id": post["id"], "modified_gmt": post.get("modified_gmt")}))
    return docs
