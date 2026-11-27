"""
Academic journals (OJS and other repositories) via OAI-PMH, Dublin Core records.

params:
  endpoint: https://vjol.info.vn/index.php/index/oai
  set: optional setSpec

Each ListRecords page → documents of title + abstract (dc:description). Full-text
PDFs are out of scope here (they go through the PDF/OCR path of phase 7).
"""
import re
from datetime import datetime
from urllib.parse import urlencode

from lxml import etree

from crawler.adapters.base import Doc, Link
from pipeline.ingest.html_dates import iso_date

DOCUMENT_KINDS = ("oai",)
_NS = {"oai": "http://www.openarchives.org/OAI/2.0/", "dc": "http://purl.org/dc/elements/1.1/"}


def _url(spec, token=None):
    p = spec["params"]
    query = {"verb": "ListRecords"}
    if token:
        query["resumptionToken"] = token
    else:
        query["metadataPrefix"] = "oai_dc"
        if p.get("set"):
            query["set"] = p["set"]
    return p["endpoint"] + "?" + urlencode(query)


def seeds(spec):
    return [Link(_url(spec), "oai", priority=10)]


def _root(body):
    return etree.fromstring(body, parser=etree.XMLParser(recover=True, huge_tree=True))


def discover(spec, page):
    root = _root(page.body)
    token = root.findtext(".//oai:resumptionToken", namespaces=_NS) if root is not None else None
    return [Link(_url(spec, token.strip()), "oai", priority=10)] if token and token.strip() else []


def extract(spec, group_key, pages):
    root = _root(pages[0].body)
    if root is None:
        return []
    bound = (pages[0].fetched_at or datetime.now()).date()
    docs = []
    for rec in root.iterfind(".//oai:record", _NS):
        dc = lambda tag: [t.strip() for t in rec.xpath(f".//dc:{tag}/text()", namespaces=_NS) if t.strip()]
        title, abstract = dc("title"), dc("description")
        url = next((u for u in dc("identifier") if re.match(r"https?://", u)), None)
        if not url or not abstract:
            continue
        published = next((d for d in (iso_date(v, bound) for v in dc("date")) if d), None)
        text = "\n\n".join(([title[0]] if title else []) + abstract)
        docs.append(Doc(url=url, text=text, title=title[0] if title else None, publish_date=published,
                        publish_date_source="meta:dc:date" if published else "none",
                        author="; ".join(dict.fromkeys(dc("creator"))) or None, tags=dc("subject"),
                        meta={"oai_id": rec.findtext(".//oai:identifier", namespaces=_NS)}))
    return docs
