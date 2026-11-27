import json
from datetime import date, datetime

from crawler.adapters import sitemap, wayback, wordpress, xenforo
from crawler.adapters.base import Page

XF = {"params": {"base": "https://forum.vn", "forums": ["/forums/"]}}


def _page(url, kind, body, seq=0, headers=None):
    body = body.encode() if isinstance(body, str) else body
    return Page(url, kind, 200, headers or {"Content-Type": "text/html; charset=utf-8"}, body, seq=seq,
                fetched_at=datetime(2026, 11, 14))


def test_xenforo_discovers_threads_forums_and_all_thread_pages():
    listing = ('<a href="/threads/hoi-dap.123/unread">t</a><a href="/threads/hoi-dap.123/page-3">p3</a>'
               '<a href="/forums/tam-su.5/page-2">next</a><a href="/members/x.9/">m</a>')
    links = {(l.url, l.kind) for l in xenforo.discover(XF, _page("https://forum.vn/forums/", "forum", listing))}
    assert links == {("https://forum.vn/threads/hoi-dap.123/", "thread"), ("https://forum.vn/forums/tam-su.5/page-2", "forum")}
    first = '<ul class="pageNav-main"><li><a href="/threads/hoi-dap.123/page-2">2</a></li><li><a href="/threads/hoi-dap.123/page-4">4</a></li></ul>'
    pages = xenforo.discover(XF, _page("https://forum.vn/threads/hoi-dap.123/", "thread", first, seq=1))
    assert [(l.seq, l.group_key) for l in pages] == [(n, "https://forum.vn/threads/hoi-dap.123/") for n in (2, 3, 4)]


def test_xenforo_thread_is_one_document_without_quotes_or_usernames():
    post = ('<article class="message" data-author="{user}"><time class="u-dt" datetime="{when}"></time>'
            '<div class="message-body"><div class="bbWrapper">{body}</div></div></article>')
    page1 = ('<h1 class="p-title-value">Hỏi về luật</h1>'
             + post.format(user="alice", when="2019-05-01T10:00:00+0700", body="Câu hỏi gốc"))
    page2 = post.format(user="bob", when="2019-05-02T10:00:00+0700",
                        body='<blockquote class="bbCodeBlock--quote">Câu hỏi gốc</blockquote>Trả lời')
    root = "https://forum.vn/threads/hoi-dap.123/"
    [doc] = xenforo.extract(XF, root, [_page(root, "thread", page1, 1), _page(root + "page-2", "thread", page2, 2)])
    assert doc.text == "Câu hỏi gốc\n\n---\n\nTrả lời"
    assert (doc.title, doc.publish_date, doc.url) == ("Hỏi về luật", date(2019, 5, 1), root)
    assert "alice" not in doc.text and "bob" not in doc.text


def test_sitemap_follows_index_and_filters_articles():
    spec = {"params": {"sitemaps": [], "include": r"-\d{6}\.chn$"}}
    index = '<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"><sitemap><loc>https://a.vn/s1.xml</loc></sitemap></sitemapindex>'
    assert [(l.url, l.kind) for l in sitemap.discover(spec, _page("https://a.vn/sitemap.xml", "sitemap", index))] == [("https://a.vn/s1.xml", "sitemap")]
    urlset = ('<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"><url><loc>https://a.vn/bai-123456.chn</loc></url>'
              '<url><loc>https://a.vn/chuyen-muc</loc></url></urlset>')
    assert [l.url for l in sitemap.discover(spec, _page("https://a.vn/s1.xml", "sitemap", urlset))] == ["https://a.vn/bai-123456.chn"]


def test_wordpress_enqueues_all_listing_pages_and_extracts_posts():
    spec = {"params": {"site": "https://blog.vn"}}
    page1 = _page("u", "wp_posts", b"[]", seq=1, headers={"X-WP-TotalPages": "3"})
    assert [l.seq for l in wordpress.discover(spec, page1)] == [2, 3]
    posts = [{"id": 7, "link": "https://blog.vn/bai/", "date_gmt": "2015-03-02T01:00:00",
              "title": {"rendered": "Ch&#7919; Vi&#7879;t"}, "content": {"rendered": "<p>Đoạn 1</p><p>Đoạn 2</p>"}}]
    [doc] = wordpress.extract(spec, "u", [_page("u", "wp_posts", json.dumps(posts))])
    assert (doc.title, doc.text, doc.publish_date) == ("Chữ Việt", "Đoạn 1\n\nĐoạn 2", date(2015, 3, 2))


def test_wayback_cdx_page_yields_raw_captures_and_resume_page():
    spec = {"params": {"url_patterns": ["forum.vn/*"]}}
    rows = [["timestamp", "original"], ["20120102030405", "http://forum.vn/t/1"], [], ["RESUME"]]
    links = wayback.discover(spec, _page(wayback._cdx_url(spec, "forum.vn/*"), "cdx", json.dumps(rows)))
    capture = links[0]
    assert (capture.url, capture.group_key, capture.captured_at) == (
        "https://web.archive.org/web/20120102030405id_/http://forum.vn/t/1", "http://forum.vn/t/1", "2012-01-02T03:04:05")
    assert links[-1].kind == "cdx" and "resumeKey=RESUME" in links[-1].url and "url=forum.vn" in links[-1].url
