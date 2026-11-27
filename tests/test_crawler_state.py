from crawler import warc_store
from crawler.adapters.base import Link
from crawler.frontier import Frontier


def test_frontier_resumes_without_refetching_done_urls(tmp_path):
    path = tmp_path / "s.sqlite"
    frontier = Frontier("s", path)
    frontier.add([Link("https://a.vn/1", "article"), Link("https://a.vn/2", "article")])
    frontier.mark_done("https://a.vn/1", 200, "warc://x#0")
    frontier.mark_failed("https://a.vn/2", "timeout")
    resumed = Frontier("s", path)  # simulates a restart after a kill
    assert resumed.add([Link("https://a.vn/1", "article")]) == 0  # rediscovery never resets state
    assert [row[0] for row in resumed.claim(10, max_attempts=3)] == ["https://a.vn/2"]
    resumed.mark_failed("https://a.vn/2", "http 404", 404, final=True)
    assert resumed.claim(10, max_attempts=3) == []


def test_done_groups_returns_thread_pages_in_order(tmp_path):
    frontier = Frontier("s", tmp_path / "s.sqlite")
    root = "https://f.vn/threads/a.1/"
    frontier.add([Link(root + "page-2", "thread", group_key=root, seq=2), Link(root, "thread", group_key=root, seq=1)])
    for url in (root, root + "page-2"):
        frontier.mark_done(url, 200, f"warc://{url}")
    [(group, members)] = list(frontier.done_groups(["thread"]))
    assert group == root and [m[2] for m in members] == [1, 2]


def test_warc_roundtrip_by_raw_ref(tmp_path, monkeypatch):
    monkeypatch.setattr(warc_store, "DATA_ROOT", tmp_path)
    monkeypatch.setattr(warc_store, "RAW_DIR", tmp_path / "raw")
    store = warc_store.WarcStore("s")
    first = store.write("https://a.vn/1", 200, {"Content-Type": "text/html"}, "Xin chào".encode())
    second = store.write("https://a.vn/2", 200, {"Content-Encoding": "gzip"}, b"two", captured_at="2012-01-02T03:04:05")
    store.close()
    assert warc_store.read(second)[0::3] == ("https://a.vn/2", b"two")
    url, status, headers, body = warc_store.read(first)
    assert (url, status, body.decode()) == ("https://a.vn/1", 200, "Xin chào")


def test_unparseable_page_is_retried_then_failed(tmp_path):
    frontier = Frontier("s", tmp_path / "s.sqlite")
    frontier.add([Link("https://idx/page0", "cc_index")])
    for _ in range(2):  # e.g. truncated JSON from an overloaded index server
        frontier.mark_done("https://idx/page0", 200, "warc://x#0")
        frontier.requeue("https://idx/page0", "discover: JSONDecodeError")
    assert frontier.claim(10, max_attempts=3)  # still has one attempt left
    frontier.mark_done("https://idx/page0", 200, "warc://x#1")
    frontier.requeue("https://idx/page0", "discover: JSONDecodeError")
    frontier.exhaust(max_attempts=3)
    assert frontier.claim(10, max_attempts=3) == [] and frontier.stats() == {"cc_index:failed": 1}


def test_429_pauses_the_whole_host():
    import asyncio
    import time
    from crawler import fetcher as fetcher_mod

    async def scenario():
        f = fetcher_mod.Fetcher(robots_exempt_hosts=("api.vn",))

        async def fake_get(url, headers=None):
            return 429, {"Retry-After": "300"}, b""
        f._raw_get = fake_get
        try:
            await f.get("https://api.vn/x")
        except fetcher_mod.FetchError as exc:
            assert not exc.final  # retried later, not dropped
        return f._next["api.vn"] - time.monotonic()

    assert asyncio.run(scenario()) > 290


def test_content_signal_ai_train_no_refuses_the_whole_site():
    import asyncio
    from crawler import fetcher as fetcher_mod
    from pipeline.net import content_signals

    voz_robots = ("User-agent: *\nContent-Signal: search=yes,ai-train=no,use=reference\nAllow: /\n\n"
                  "User-agent: GPTBot\nDisallow: /\n")
    assert content_signals(voz_robots) == {"search": "yes", "ai-train": "no", "use": "reference"}
    assert content_signals("User-agent: GPTBot\nContent-Signal: ai-train=no\n") == {}  # other bots' rules don't apply

    async def scenario():
        f = fetcher_mod.Fetcher(rps=1000)

        async def fake_get(url, headers=None):
            return 200, {}, voz_robots.encode()
        f._raw_get = fake_get
        try:
            await f.get("https://voz.vn/t/x.1/")
        except fetcher_mod.FetchError as exc:
            return str(exc), exc.final

    assert asyncio.run(scenario()) == ("ai_train_refused", True)
