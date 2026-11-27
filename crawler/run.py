"""
Crawl one source described by manifests/crawl/<source_id>.yaml.

    uv run python -m crawler.run crawl <source_id> [--max-pages N]
    uv run python -m crawler.run status <source_id>

Loop: claim pending URLs → fetch politely → write raw response to WARC → mark done
→ enqueue discovered links. Safe to kill at any time; re-running resumes from the
frontier. Extraction to canonical is a separate step (crawler.extract).
"""
import argparse
import asyncio
import sys
from datetime import datetime

import yaml

from crawler.adapters import ADAPTERS
from crawler.adapters.base import Page
from crawler.fetcher import FetchError, Fetcher
from crawler.frontier import Frontier
from crawler.warc_store import WarcStore
from pipeline.paths import ROOT

SPEC_DIR = ROOT / "manifests" / "crawl"
CLAIM_BATCH = 50
REFUSALS = ("robots_disallow", "ai_train_refused")  # site said no: recorded as skipped, never retried


def load_spec(source_id):
    spec = yaml.safe_load((SPEC_DIR / f"{source_id}.yaml").read_text(encoding="utf-8"))
    spec.setdefault("rps", 1.0)
    spec.setdefault("client", "httpx")
    spec.setdefault("max_attempts", 3)
    spec.setdefault("concurrency", 2)
    spec.setdefault("timeout", 30)
    return spec


async def _fetch_one(spec, adapter, fetcher, frontier, warc, row, stats):
    url, kind, depth, group_key, seq, captured_at = row
    try:
        status, headers, body = await fetcher.get(url)
    except FetchError as exc:
        if str(exc) in REFUSALS:
            frontier.mark_skipped(url, str(exc))
        else:
            frontier.mark_failed(url, str(exc), exc.status, final=exc.final)
        stats["failed"] += 1
        return
    raw_ref = warc.write(url, status, headers, body, captured_at)
    frontier.mark_done(url, status, raw_ref)
    page = Page(url, kind, status, headers, body, seq=seq, fetched_at=datetime.now(),
                captured_at=datetime.fromisoformat(captured_at) if captured_at else None, raw_ref=raw_ref)
    try:
        stats["discovered"] += frontier.add(adapter.discover(spec, page), depth + 1)
    except Exception as exc:  # truncated/malformed page: retry until max_attempts, then it stays failed
        frontier.requeue(url, f"discover: {type(exc).__name__}: {exc}"[:300])
    stats["fetched"] += 1


async def crawl(source_id, max_pages, kinds=None):
    spec = load_spec(source_id)
    adapter = ADAPTERS[spec["adapter"]]
    frontier, warc = Frontier(source_id), WarcStore(source_id)
    frontier.add(adapter.seeds(spec))
    stats = {"fetched": 0, "failed": 0, "discovered": 0}
    async with Fetcher(spec["client"], spec["rps"], timeout=spec["timeout"],
                       robots_exempt_hosts=getattr(adapter, "ROBOTS_EXEMPT_HOSTS", ())) as fetcher:
        try:
            while not max_pages or stats["fetched"] < max_pages:
                rows = frontier.claim(min(CLAIM_BATCH, max_pages - stats["fetched"]) if max_pages else CLAIM_BATCH,
                                      spec["max_attempts"], kinds)
                if not rows:
                    break
                semaphore = asyncio.Semaphore(spec["concurrency"])

                async def guarded(row):
                    async with semaphore:
                        await _fetch_one(spec, adapter, fetcher, frontier, warc, row, stats)

                await asyncio.gather(*(guarded(r) for r in rows))
                print(f"{source_id}: {stats}", flush=True)
        finally:
            warc.close()
            frontier.exhaust(spec["max_attempts"])
    print(f"{source_id}: finished {stats}; frontier {frontier.stats()}")


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["crawl", "status"])
    parser.add_argument("source_id")
    parser.add_argument("--max-pages", type=int, default=0, help="stop after N fetched pages (0 = until done)")
    parser.add_argument("--kinds", help="only fetch these kinds, comma-separated (e.g. sitemap,listing,forum "
                                        "for a discovery-only pass)")
    args = parser.parse_args()
    if args.command == "status":
        for key, count in sorted(Frontier(args.source_id).stats().items()):
            print(f"{key:30} {count}")
    else:
        asyncio.run(crawl(args.source_id, args.max_pages, args.kinds.split(",") if args.kinds else None))


if __name__ == "__main__":
    main()
