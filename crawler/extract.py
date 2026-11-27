"""
Offline extraction: WARC records of a crawled source → canonical DocumentRecords.

    uv run python -m crawler.extract <source_id>

Reads only local state (frontier + WARC), never the network, so it can be re-run
whenever an adapter's extraction improves. Output: canonical/<source_id>/crawl/.
"""
import argparse
import json
import sys
import unicodedata
from datetime import datetime

from crawler import warc_store
from crawler.adapters import ADAPTERS
from crawler.adapters.base import Page
from crawler.frontier import Frontier
from crawler.run import load_spec
from pipeline.ingest.canonical_writer import ShardWriter
from pipeline.paths import REPORTS_DIR
from pipeline.schema import sources_manifest
from pipeline.schema.document_record import canonical_url, era_for, make_doc_id, text_sha256, validate

EXTRACTOR_VERSION = 1


def to_record(source, adapter_name, doc, first_page):
    crawl_time = first_page.captured_at or first_page.fetched_at
    published = doc.publish_date if doc.publish_date and doc.publish_date <= crawl_time.date() else None
    url = canonical_url(doc.url)
    text = unicodedata.normalize("NFC", doc.text)
    return {
        "doc_id": make_doc_id(source["id"], doc.locator or url), "source_id": source["id"], "source_type": source["source_type"],
        "url": url, "title": unicodedata.normalize("NFC", doc.title) if doc.title else None, "text": text,
        "lang": None, "lang_score": None, "publish_date": published,
        "publish_date_source": doc.publish_date_source if published else "none",
        "crawl_time": crawl_time, "era": era_for(published, crawl_time), "author": doc.author,
        "category": doc.category, "tags": doc.tags, "raw_ref": first_page.raw_ref,
        "text_sha256": text_sha256(text), "license": source["license"], "usage_scope": source["usage_scope"],
        "extractor": f"crawler.{adapter_name}@{EXTRACTOR_VERSION}",
        "meta": json.dumps(doc.meta, ensure_ascii=False, default=str),
    }


def _pages(members):
    pages = []
    for url, kind, seq, raw_ref, fetched_at, captured_at in members:
        _, status, headers, body = warc_store.read(raw_ref)
        pages.append(Page(url, kind, status, headers, body, seq=seq, fetched_at=datetime.fromisoformat(fetched_at),
                          captured_at=datetime.fromisoformat(captured_at) if captured_at else None, raw_ref=raw_ref))
    return pages


def extract(source_id):
    spec = load_spec(source_id)
    adapter = ADAPTERS[spec["adapter"]]
    frontier = Frontier(source_id)
    if hasattr(adapter, "build_context"):  # source-wide facts extraction needs (e.g. tinhte promoted ids)
        spec = {**spec, "context": adapter.build_context(
            spec, ((g, _pages(m)) for g, m in frontier.done_groups(adapter.CONTEXT_KINDS)))}
    source = next(e for e in sources_manifest.load() if e["id"] == source_id)
    stats = {"groups": 0, "written": 0, "empty": 0, "invalid": 0, "duplicate": 0}
    seen, writer = set(), ShardWriter(source_id, "crawl")
    try:
        for group_key, members in frontier.done_groups(adapter.DOCUMENT_KINDS):
            stats["groups"] += 1
            pages = _pages(members)
            docs = adapter.extract(spec, group_key, pages)
            stats["empty"] += not docs
            for doc in docs:
                record = to_record(source, spec["adapter"], doc, pages[0])
                if record["doc_id"] in seen:
                    stats["duplicate"] += 1
                elif validate(record):
                    stats["invalid"] += 1
                else:
                    seen.add(record["doc_id"])
                    writer.write(record)
                    stats["written"] += 1
        writer.commit()
    except BaseException:
        writer.abort()
        raise
    (REPORTS_DIR / "crawl").mkdir(parents=True, exist_ok=True)
    (REPORTS_DIR / "crawl" / f"{source_id}.extract.json").write_text(json.dumps(stats, indent=1), encoding="utf-8")
    print(f"{source_id}: {stats}")


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("source_ids", nargs="+")
    for source_id in parser.parse_args().source_ids:
        extract(source_id)


if __name__ == "__main__":
    main()
