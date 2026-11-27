"""
Adapter contract. An adapter is a module exposing:

    DOCUMENT_KINDS: tuple[str, ...]           kinds whose fetched pages become documents
    seeds(spec) -> list[Link]                  starting URLs
    discover(spec, page) -> list[Link]         links found in a fetched page (any kind)
    extract(spec, group_key, pages) -> list[Doc]
        pages: list[Page] of one group (e.g. all pages of a forum thread) in seq order

Fetching and extraction are separate passes: discover() runs at crawl time, extract()
runs later over WARC, so extraction can be improved and re-run without refetching.
"""
from dataclasses import dataclass, field
from datetime import date, datetime


@dataclass(frozen=True)
class Link:
    url: str
    kind: str
    priority: int = 0
    group_key: str | None = None
    seq: int = 0
    captured_at: str | None = None  # ISO time of an archive capture (Wayback/CC)


@dataclass
class Page:
    url: str
    kind: str
    status: int
    headers: dict
    body: bytes
    seq: int = 0
    fetched_at: datetime | None = None
    captured_at: datetime | None = None
    raw_ref: str | None = None

    def text(self):
        charset = "utf-8"
        content_type = next((v for k, v in self.headers.items() if k.lower() == "content-type"), "")
        if "charset=" in content_type:
            charset = content_type.split("charset=")[-1].split(";")[0].strip() or "utf-8"
        return self.body.decode(charset, errors="replace")


@dataclass
class Doc:
    """Adapter output; crawler.extract turns it into a validated DocumentRecord."""
    url: str
    text: str
    title: str | None = None
    publish_date: date | None = None
    publish_date_source: str = "none"
    author: str | None = None
    category: str | None = None
    tags: list = field(default_factory=list)
    meta: dict = field(default_factory=dict)
    locator: str | None = None  # doc_id locator when one URL yields several documents (default: url)