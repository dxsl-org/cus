"""
Polite async HTTP fetcher shared by all adapters.

- One request at a time per host at most `rps` per second (per-host lock + clock).
- robots.txt honoured per origin (fetched once, through the same client).
- "#bytes=a-b" URL fragments become Range requests (Common Crawl WARC slices).
- Two client backends: "httpx" (default, OS trust store) and "impersonate"
  (curl_cffi with a Chrome TLS fingerprint, for sites that block non-browser clients).
"""
import asyncio
import time
from urllib.parse import urlsplit

import httpx

from pipeline.net import DEFAULT_HEADERS, TLS_CONTEXT, USER_AGENT, content_signals, parse_robots

MAX_BODY_BYTES = 20 * 2**20
BACKOFF_SECONDS = 120


class FetchError(Exception):
    def __init__(self, message, status=None, final=False):
        super().__init__(message)
        self.status, self.final = status, final


class Fetcher:
    def __init__(self, client="httpx", rps=1.0, timeout=30, robots_exempt_hosts=()):
        """
        robots_exempt_hosts: dataset/API hosts whose robots.txt targets crawlers while their
        terms explicitly invite programmatic download (Common Crawl). Never a website.
        """
        self.client_kind, self.interval, self.timeout = client, 1.0 / rps, timeout
        self.robots_exempt_hosts = frozenset(robots_exempt_hosts)
        self._robots, self._next, self._locks = {}, {}, {}

    async def __aenter__(self):
        if self.client_kind == "impersonate":
            from curl_cffi.requests import AsyncSession
            # Keep curl_cffi's own User-Agent: a UA that disagrees with the TLS fingerprint gets challenged.
            self._client = AsyncSession(impersonate="chrome", timeout=self.timeout,
                                        headers={"Accept-Language": DEFAULT_HEADERS["Accept-Language"]})
        else:
            self._client = httpx.AsyncClient(headers=DEFAULT_HEADERS, timeout=self.timeout,
                                             follow_redirects=True, verify=TLS_CONTEXT)
        return self

    async def __aexit__(self, *exc):
        await self._client.close() if self.client_kind == "impersonate" else await self._client.aclose()

    async def _raw_get(self, url, headers=None):
        response = await self._client.get(url, headers=headers or {}, **({"allow_redirects": True}
                                                                          if self.client_kind == "impersonate" else {}))
        return response.status_code, dict(response.headers), response.content

    async def _polite(self, host):
        lock = self._locks.setdefault(host, asyncio.Lock())
        async with lock:
            wait = self._next.get(host, 0) - time.monotonic()
            if wait > 0:
                await asyncio.sleep(wait)
            self._next[host] = time.monotonic() + self.interval

    async def refusal(self, url):
        """None if `url` may be fetched, else the reason: "robots_disallow" or "ai_train_refused"."""
        parts = urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        if origin not in self._robots:
            await self._polite(parts.netloc)
            try:
                status, _, body = await self._raw_get(origin + "/robots.txt")
                text = body.decode("utf-8", errors="replace") if status == 200 else ""
            except Exception:  # unreachable robots.txt → treat as no restrictions (RFC 9309)
                status, text = 0, ""
            self._robots[origin] = (parse_robots(status, text), content_signals(text))
        parser, signals = self._robots[origin]
        if signals.get("ai-train") == "no":
            return "ai_train_refused"  # the whole point of this crawl is training data
        return None if parser.can_fetch(USER_AGENT, url) else "robots_disallow"

    async def get(self, url):
        """Return (status, headers, body) for a 200/206; raise FetchError otherwise."""
        target, _, fragment = url.partition("#")
        headers = {"Range": fragment} if fragment.startswith("bytes=") else None
        if urlsplit(target).netloc not in self.robots_exempt_hosts and (reason := await self.refusal(target)):
            raise FetchError(reason, final=True)
        host = urlsplit(target).netloc
        await self._polite(host)
        try:
            status, resp_headers, body = await self._raw_get(target, headers)
        except Exception as exc:  # network errors from either backend are retryable
            raise FetchError(f"{type(exc).__name__}: {exc}"[:300]) from exc
        if status in (200, 206):
            if len(body) > MAX_BODY_BYTES:
                raise FetchError("body_too_large", status=status, final=True)
            return status, resp_headers, body
        if status in (429, 503):
            # Server asked us to slow down: pause the whole host, not just this URL.
            retry_after = next((v for k, v in resp_headers.items() if k.lower() == "retry-after"), "")
            pause = max(int(retry_after) if retry_after.isdigit() else 0, BACKOFF_SECONDS)
            self._next[host] = max(self._next.get(host, 0), time.monotonic() + pause)
        raise FetchError(f"http {status}", status=status, final=status in (401, 403, 404, 410, 451))
