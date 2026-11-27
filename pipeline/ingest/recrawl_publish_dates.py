"""
Recover publish dates for canonical docs that lack one by re-fetching the page
<head> and reading machine-readable metadata. Results go to the patch store;
re-run the exporter for the source afterwards to merge them.

    uv run python -m pipeline.ingest.recrawl_publish_dates plo_vn --limit 50
    uv run python -m pipeline.ingest.recrawl_publish_dates plo_vn baochinhphu_vn nhandan_vn

Politeness: obeys robots.txt, one host at a time per source, `--rps` requests/s
per host (default 1). Resumable: docs already in the patch store are skipped
(failed lookups too, unless --retry-failed). Ctrl+C flushes pending results.
"""
import argparse
import asyncio
import re
import sys
import time

import httpx
import pyarrow.compute as pc
import pyarrow.dataset as ds

from pipeline.ingest.html_dates import extract_publish_date
from pipeline.ingest.patch_store import PUBLISH_DATES
from pipeline.net import DEFAULT_HEADERS, TLS_CONTEXT, USER_AGENT, parse_robots
from pipeline.paths import CANONICAL_DIR

MAX_HEAD_BYTES = 256 * 1024
FLUSH_EVERY = 200


def _pending_docs(source_id, retry_failed, limit):
    table = ds.dataset(CANONICAL_DIR / source_id, format="parquet").to_table(
        columns=["doc_id", "url", "crawl_time"],
        filter=pc.field("publish_date").is_null() & pc.field("url").is_valid())
    done = PUBLISH_DATES.load(source_id)
    rows = [r for r in table.to_pylist()
            if r["doc_id"] not in done or (retry_failed and done[r["doc_id"]]["publish_date"] is None)]
    return rows[:limit] if limit else rows


async def _fetch_head(client, url):
    async with client.stream("GET", url) as response:
        if response.status_code != 200:
            return None, f"http {response.status_code}"
        chunks, size = [], 0
        async for chunk in response.aiter_bytes():
            chunks.append(chunk)
            size += len(chunk)
            if size >= MAX_HEAD_BYTES or b"</head>" in chunk:
                break
        return b"".join(chunks).decode(response.encoding or "utf-8", errors="replace"), None


def _robots(client_sync, url):
    origin = re.match(r"https?://[^/]+", url)[0]
    try:
        response = client_sync.get(origin + "/robots.txt")
        return parse_robots(response.status_code, response.text)
    except httpx.HTTPError:
        return parse_robots(0, "")


async def recrawl_source(source_id, rps, retry_failed, limit):
    docs = _pending_docs(source_id, retry_failed, limit)
    print(f"{source_id}: {len(docs)} docs to look up", flush=True)
    if not docs:
        return
    headers = DEFAULT_HEADERS
    with httpx.Client(headers=headers, timeout=20, follow_redirects=True, verify=TLS_CONTEXT) as sync_client:
        robots = _robots(sync_client, docs[0]["url"])
    pending, stats, interval = [], {"dated": 0, "failed": 0}, 1.0 / rps
    async with httpx.AsyncClient(headers=headers, timeout=30, follow_redirects=True, verify=TLS_CONTEXT) as client:
        try:
            for i, doc in enumerate(docs, 1):
                started = time.monotonic()
                not_after = doc["crawl_time"].date()
                if not robots.can_fetch(USER_AGENT, doc["url"]):
                    found, detail = None, "robots_disallow"
                else:
                    try:
                        html, error = await _fetch_head(client, doc["url"])
                        found, detail = (None, error) if error else extract_publish_date(html, not_after)
                    except httpx.HTTPError as exc:
                        found, detail = None, f"error {type(exc).__name__}"
                    except Exception as exc:  # noqa: BLE001 - one malformed page must not end a multi-day run
                        found, detail = None, f"error {type(exc).__name__}"
                stats["dated" if found else "failed"] += 1
                pending.append({"doc_id": doc["doc_id"], "publish_date": found, "tag": "recrawl", "detail": detail})
                if len(pending) >= FLUSH_EVERY:
                    PUBLISH_DATES.append(source_id, pending)
                    pending = []
                    print(f"{source_id}: {i}/{len(docs)} dated={stats['dated']} failed={stats['failed']}", flush=True)
                await asyncio.sleep(max(0.0, interval - (time.monotonic() - started)))
        finally:
            PUBLISH_DATES.append(source_id, pending)
    print(f"{source_id}: done dated={stats['dated']} failed={stats['failed']}", flush=True)


async def _main(args):
    # Different sources are different hosts: run them concurrently, each politely serial.
    await asyncio.gather(*(recrawl_source(s, args.rps, args.retry_failed, args.limit) for s in args.sources))


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("sources", nargs="+")
    parser.add_argument("--rps", type=float, default=0.5, help="requests per second per host")
    parser.add_argument("--limit", type=int, default=0, help="max docs per source (0 = all)")
    parser.add_argument("--retry-failed", action="store_true")
    asyncio.run(_main(parser.parse_args()))


if __name__ == "__main__":
    main()
