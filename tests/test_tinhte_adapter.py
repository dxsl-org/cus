import json
from datetime import datetime

from crawler.adapters import tinhte
from crawler.adapters.base import Page

ROOT = "https://tinhte.vn/thread/pixel-va-iphone.4191342/"


def _thread_html(posts, pages=1, mod=False):
    job = {"thread": {"thread_id": "4191342", "thread_title": "Pixel và iPhone", "forum_id": "796",
                      "creator_is_mod": str(mod), "creator_has_verified_badge": "False", "thread_tags": {"1": "pixel"}},
           "posts": posts, "links": {"pages": pages}}
    data = {"props": {"pageProps": {"apiData": {"jobs": {"x": job}}}}}
    return f'<script id="__NEXT_DATA__" type="application/json">{json.dumps(data)}</script>'


def _post(pid, first, body, ts):
    return {"post_id": pid, "post_is_first_post": str(first), "post_body_html": body, "post_create_date": str(ts)}


def _page(url, kind, body, seq=1):
    return Page(url, kind, 200, {"Content-Type": "text/html"}, body.encode(), seq=seq, fetched_at=datetime(2026, 11, 14))


def test_thread_split_into_opening_post_and_replies():
    html = _thread_html([_post(1, True, "<p>Bài dịch từ The Verge</p>", 1700000000),
                         _post(2, False, "<blockquote>Bài dịch</blockquote>Mình thấy Pixel chụp đẹp hơn", 1700000100),
                         _post(2, False, "<blockquote>Bài dịch</blockquote>Mình thấy Pixel chụp đẹp hơn", 1700000100)])
    spec = {"params": {}, "context": {"promoted": {4191342}}}
    op, replies = tinhte.extract(spec, ROOT, [_page(ROOT, "thread", html)])
    assert (op.text, op.meta["content_role"], op.meta["promoted"]) == ("Bài dịch từ The Verge", "editorial", True)
    assert (replies.text, replies.meta["content_role"], replies.meta["replies"]) == ("Mình thấy Pixel chụp đẹp hơn", "user_reply", 1)
    assert op.locator != replies.locator  # two documents, two doc_ids


def test_user_thread_not_promoted_is_user_op_and_mod_is_editorial():
    html = _thread_html([_post(1, True, "Hỏi về máy", 1700000000)])
    assert tinhte.extract({"params": {}, "context": {"promoted": set()}}, ROOT, [_page(ROOT, "thread", html)])[0].meta["content_role"] == "user_op"
    html_mod = _thread_html([_post(1, True, "Bài của mod", 1700000000)], mod=True)
    assert tinhte.extract({"params": {}, "context": {"promoted": set()}}, ROOT, [_page(ROOT, "thread", html_mod)])[0].meta["content_role"] == "editorial"


def test_discovers_thread_pages_and_promoted_feed_chain():
    pages = tinhte.discover({}, _page(ROOT, "thread", _thread_html([], pages=3), seq=1))
    assert [(l.url, l.seq, l.group_key) for l in pages] == [(ROOT + "page-2/", 2, ROOT), (ROOT + "page-3/", 3, ROOT)]
    feed = tinhte.PROMOTED.format(page=7, token="T")
    body = json.dumps({"threads": [{"thread_id": 1}], "threads_total": 127711})
    [nxt] = tinhte.discover({}, Page(feed, "promoted", 200, {}, body.encode(), seq=7))
    assert (nxt.kind, nxt.seq, "oauth_token=T" in nxt.url) == ("promoted", 8, True)
    refresh_at = tinhte.PROMOTED.format(page=tinhte.TOKEN_PAGES, token="T")
    [home] = tinhte.discover({}, Page(refresh_at, "promoted", 200, {}, body.encode(), seq=tinhte.TOKEN_PAGES))
    assert (home.kind, home.url) == ("home", f"{tinhte.HOME}?feed_page={tinhte.TOKEN_PAGES + 1}")
