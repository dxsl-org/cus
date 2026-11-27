"""
Export downloaded Hugging Face Vietnamese legal datasets into canonical DocumentRecords:
- hf_phapdien_tmquan (tmquan/phapdien-moj-gov-vn)
- hf_legal_vohuutridung (vohuutridung/vietnamese-legal-documents)
- hf_legal_th1nhng0 (th1nhng0/vietnamese-legal-documents)
- hf_legal_vlsp2025 (VLSP2025-LegalSML/legal-pretrain)

    uv run python -m pipeline.ingest.legal_open_export [names...] [--workers 4]
"""
import argparse
import json
import re
import sys
import unicodedata
from concurrent.futures import ProcessPoolExecutor
from datetime import date, datetime, timezone
from pathlib import Path

import pyarrow.parquet as pq
from lxml import html as lxml_html

from pipeline.acquire.hf_snapshot import MANIFEST_DIR, load_declarations, target_dir
from pipeline.ingest.canonical_writer import ShardWriter, publish, staging_dir
from pipeline.paths import DATA_ROOT, RAW_DIR, REPORTS_DIR
from pipeline.schema.document_record import era_for, make_doc_id, text_sha256, validate

_DMY_RE = re.compile(r"(\d{1,2})/(\d{1,2})/(\d{4})")
_ISO_RE = re.compile(r"(\d{4})-(\d{1,2})-(\d{1,2})")
_BLOCK_TAGS = {"p", "div", "li", "ul", "ol", "h1", "h2", "h3", "h4", "h5", "h6", "blockquote", "pre",
               "table", "tr", "section", "article", "header", "footer"}
_BLANK_LINES = re.compile(r"\n{3,}")


def nfc(text):
    if not text:
        return ""
    return text if unicodedata.is_normalized("NFC", text) else unicodedata.normalize("NFC", text)


def parse_legal_date(value, bound):
    """Parse dd/mm/yyyy, yyyy-mm-dd, date, or datetime within [1945-01-01, bound]."""
    if value is None:
        return None
    found = None
    if isinstance(value, datetime):
        found = value.date()
    elif isinstance(value, date):
        found = value
    else:
        s = str(value).strip()
        if not s:
            return None
        if m := _ISO_RE.search(s):
            try:
                found = date(int(m[1]), int(m[2]), int(m[3]))
            except ValueError:
                return None
        elif m := _DMY_RE.search(s):
            try:
                found = date(int(m[3]), int(m[2]), int(m[1]))
            except ValueError:
                return None
    if found and date(1945, 1, 1) <= found <= bound:
        return found
    return None


def html_to_legal_text(raw_html):
    """Extract clean NFC text from full HTML documents or fragments, keeping clause/table breaks."""
    if not raw_html or not raw_html.strip():
        return ""
    doc = lxml_html.fromstring(raw_html) if "<html" in raw_html.lower() or "<body" in raw_html.lower() else lxml_html.fragment_fromstring(raw_html, create_parent="div")
    root = doc.find(".//body") if hasattr(doc, "find") else None
    if root is None:
        root = doc
    for node in root.iter("script", "style", "noscript", "head"):
        node.drop_tree()
    for br in root.iter("br"):
        br.tail = "\n" + (br.tail or "")
    for td in root.iter("td", "th"):
        td.tail = " | " + (td.tail or "")
    for node in root.iter():
        if isinstance(node.tag, str) and node.tag in _BLOCK_TAGS:
            node.tail = "\n\n" + (node.tail or "")
    text = root.text_content()
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.splitlines()]
    return nfc(_BLANK_LINES.sub("\n\n", "\n".join(lines))).strip()


def lock_crawl_time(name):
    lock_path = MANIFEST_DIR / f"{name}.lock.json"
    if lock_path.exists():
        dl = json.loads(lock_path.read_text(encoding="utf-8")).get("downloaded_at")
        if dl:
            return datetime.fromisoformat(dl.replace("Z", "+00:00")).astimezone(timezone.utc).replace(tzinfo=None)
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _make_record(source_id, locator, text, crawl_time, *, title=None, url=None, publish_date=None,
                 author=None, category=None, tags=None, raw_ref="", extractor="", meta=None):
    text = nfc(text).strip()
    if not text:
        return None
    title_c = nfc(title).strip() if title else None
    if title_c == "":
        title_c = None
    bound = crawl_time.date()
    pub = publish_date if (publish_date and date(1945, 1, 1) <= publish_date <= bound) else None
    era = era_for(pub, crawl_time)
    return {
        "doc_id": make_doc_id(source_id, locator),
        "source_id": source_id,
        "source_type": "legal",
        "url": url or None,
        "title": title_c,
        "text": text,
        "lang": None,
        "lang_score": None,
        "publish_date": pub,
        "publish_date_source": "dump" if pub else "none",
        "crawl_time": crawl_time,
        "era": era,
        "author": nfc(author).strip() if author else None,
        "category": nfc(category).strip() if category else None,
        "tags": [nfc(t).strip() for t in (tags or []) if t and str(t).strip()],
        "raw_ref": raw_ref,
        "text_sha256": text_sha256(text),
        "license": "vn-not-protected",
        "usage_scope": "train_ok",
        "extractor": extractor,
        "meta": json.dumps(meta or {}, ensure_ascii=False, default=str),
    }


def _export_phapdien(decl):
    name = "hf_phapdien_tmquan"
    base = target_dir(decl)
    files = sorted(base.glob("documents-*.parquet"))
    if not files:
        raise FileNotFoundError(f"No documents-*.parquet in {base}")
    writer = ShardWriter(name, "hf")
    stats = {"rows": 0, "written": 0, "empty": 0, "invalid": 0, "era": {}}
    ext = f"{name}@{decl['revision'][:12]}"
    fallback_ct = lock_crawl_time(name)
    try:
        for f in files:
            rel = f.relative_to(DATA_ROOT).as_posix()
            row_no = 0
            for batch in pq.ParquetFile(f).iter_batches(batch_size=2000):
                for r in batch.to_pylist():
                    row_no += 1
                    stats["rows"] += 1
                    ct_raw = r.get("scraped_at")
                    ct = (datetime.fromisoformat(str(ct_raw).replace("Z", "+00:00"))
                          .astimezone(timezone.utc).replace(tzinfo=None) if ct_raw else fallback_ct)
                    parts = [
                        f"Chủ đề {r.get('topic_number')}: {r.get('topic_title_vi')}" if r.get("topic_title_vi") else "",
                        f"Đề mục {r.get('subject_number')}: {r.get('subject_title_vi')}" if r.get("subject_title_vi") else "",
                        r.get("chapter_title") or "",
                        r.get("article_title") or "",
                        r.get("source_note_text") or "",
                        "",
                        r.get("content_text") or "",
                    ]
                    text = "\n".join(parts).strip()
                    rec = _make_record(
                        name, f"{decl['repo']}#{r.get('record_id') or f'{f.name}:{row_no}'}", text, ct,
                        title=r.get("article_title") or r.get("article_id"),
                        url=r.get("source_url"),
                        category=r.get("topic_title_vi"),
                        tags=[r.get("subject_title_vi")] if r.get("subject_title_vi") else [],
                        raw_ref=f"{rel}#row={row_no}",
                        extractor=ext,
                        meta={
                            "article_id": r.get("article_id"),
                            "subject_title_vi": r.get("subject_title_vi"),
                            "source_note_text": r.get("source_note_text"),
                            "related_note_text": r.get("related_note_text"),
                        },
                    )
                    if rec is None:
                        stats["empty"] += 1
                    elif validate(rec):
                        stats["invalid"] += 1
                    else:
                        writer.write(rec)
                        stats["written"] += 1
                        stats["era"][rec["era"]] = stats["era"].get(rec["era"], 0) + 1
        writer.commit()
    except BaseException:
        writer.abort()
        raise
    return stats


def _export_vohuutridung(decl):
    name = "hf_legal_vohuutridung"
    base = target_dir(decl)
    ct = lock_crawl_time(name)
    bound = ct.date()
    meta_by_id = {}
    for mf in sorted((base / "metadata").glob("*.parquet")):
        for batch in pq.ParquetFile(mf).iter_batches(batch_size=5000):
            for m in batch.to_pylist():
                meta_by_id[str(m["id"])] = (
                    m.get("document_number"), m.get("title"), m.get("url"),
                    m.get("legal_type"), m.get("legal_sectors"),
                    m.get("issuing_authority"), m.get("issuance_date"), m.get("signers"),
                )
    files = sorted((base / "content").glob("*.parquet"))
    if not files:
        raise FileNotFoundError(f"No content/*.parquet in {base}")
    writer = ShardWriter(name, "hf")
    stats = {"rows": 0, "written": 0, "empty": 0, "invalid": 0, "with_meta": 0, "with_pubdate": 0, "era": {}}
    ext = f"{name}@{decl['revision'][:12]}"
    try:
        for f in files:
            rel = f.relative_to(DATA_ROOT).as_posix()
            row_no = 0
            for batch in pq.ParquetFile(f).iter_batches(batch_size=2000):
                for r in batch.to_pylist():
                    row_no += 1
                    stats["rows"] += 1
                    doc_id_key = str(r["id"])
                    m = meta_by_id.get(doc_id_key)
                    pub = None
                    if m:
                        stats["with_meta"] += 1
                        doc_num, title, url, ltype, sectors, auth, idate, signers = m
                        pub = parse_legal_date(idate, bound)
                        if pub:
                            stats["with_pubdate"] += 1
                        tags = [s.strip() for s in str(sectors or "").split(",") if s.strip()]
                        meta = {"id": doc_id_key, "document_number": doc_num, "signers": signers}
                    else:
                        title = url = ltype = auth = None
                        tags = []
                        meta = {"id": doc_id_key}
                    rec = _make_record(
                        name, f"{decl['repo']}#{doc_id_key}", r.get("content") or "", ct,
                        title=title, url=url, publish_date=pub, author=auth, category=ltype,
                        tags=tags, raw_ref=f"{rel}#row={row_no}", extractor=ext, meta=meta,
                    )
                    if rec is None:
                        stats["empty"] += 1
                    elif validate(rec):
                        stats["invalid"] += 1
                    else:
                        writer.write(rec)
                        stats["written"] += 1
                        stats["era"][rec["era"]] = stats["era"].get(rec["era"], 0) + 1
            print(f"{name}: processed {f.name} -> {stats['written']}/{stats['rows']}", flush=True)
        writer.commit()
    except BaseException:
        writer.abort()
        raise
    return stats


def _th1nhng0_worker(args):
    batch_rows, rel, start_row, meta_sub, rel_sub, decl_repo, ext, ct_iso = args
    ct = datetime.fromisoformat(ct_iso)
    bound = ct.date()
    out_recs = []
    empty = invalid = 0
    for idx, r in enumerate(batch_rows):
        row_no = start_row + idx
        key = str(r["id"])
        text = html_to_legal_text(r.get("content_html") or "")
        if not text:
            empty += 1
            continue
        m = meta_sub.get(key) or {}
        pub = parse_legal_date(m.get("ngay_ban_hanh"), bound)
        tags = [v for v in (m.get("linh_vuc"), m.get("nganh")) if v and v != "Chưa phân loại"]
        meta_obj = {
            "id": key,
            "so_ky_hieu": m.get("so_ky_hieu"),
            "tinh_trang_hieu_luc": m.get("tinh_trang_hieu_luc"),
            "ngay_co_hieu_luc": m.get("ngay_co_hieu_luc"),
            "ngay_het_hieu_luc": m.get("ngay_het_hieu_luc"),
            "nguon_thu_thap": m.get("nguon_thu_thap"),
            "pham_vi": m.get("pham_vi"),
        }
        if key in rel_sub:
            meta_obj["relationships"] = rel_sub[key]
        rec = _make_record(
            "hf_legal_th1nhng0", f"{decl_repo}#{key}", text, ct,
            title=m.get("title"), publish_date=pub, author=m.get("co_quan_ban_hanh"),
            category=m.get("loai_van_ban"), tags=tags,
            raw_ref=f"{rel}#row={row_no}", extractor=ext, meta=meta_obj,
        )
        if rec is None:
            empty += 1
        elif validate(rec):
            invalid += 1
        else:
            out_recs.append(rec)
    return out_recs, empty, invalid


def _export_th1nhng0(decl, workers):
    name = "hf_legal_th1nhng0"
    base = target_dir(decl) / "data"
    ct = lock_crawl_time(name)
    meta_by_id = {}
    for batch in pq.ParquetFile(base / "metadata.parquet").iter_batches(batch_size=5000):
        for m in batch.to_pylist():
            meta_by_id[str(m["id"])] = m
    rels_by_id = {}
    if (base / "relationships.parquet").exists():
        for batch in pq.ParquetFile(base / "relationships.parquet").iter_batches(batch_size=10000):
            for r in batch.to_pylist():
                rels_by_id.setdefault(str(r["doc_id"]), []).append([r["relationship"], str(r["other_doc_id"])])

    content_path = base / "content.parquet"
    rel = content_path.relative_to(DATA_ROOT).as_posix()
    ext = f"{name}@{decl['revision'][:12]}"
    writer = ShardWriter(name, "hf")
    stats = {"rows": 0, "written": 0, "empty": 0, "invalid": 0, "era": {}}

    def _jobs():
        row_no = 1
        for batch in pq.ParquetFile(content_path).iter_batches(batch_size=1000):
            rows = batch.to_pylist()
            keys = {str(r["id"]) for r in rows}
            yield (
                rows, rel, row_no,
                {k: meta_by_id[k] for k in keys if k in meta_by_id},
                {k: rels_by_id[k] for k in keys if k in rels_by_id},
                decl["repo"], ext, ct.isoformat(),
            )
            row_no += len(rows)

    try:
        with ProcessPoolExecutor(max_workers=min(workers, 4)) as pool:
            for recs, empty, invalid in pool.map(_th1nhng0_worker, _jobs()):
                stats["empty"] += empty
                stats["invalid"] += invalid
                stats["rows"] += len(recs) + empty + invalid
                for rec in recs:
                    writer.write(rec)
                    stats["written"] += 1
                    stats["era"][rec["era"]] = stats["era"].get(rec["era"], 0) + 1
                if stats["rows"] % 20000 == 0:
                    print(f"{name}: {stats}", flush=True)
        writer.commit()
    except BaseException:
        writer.abort()
        raise
    return stats


def _vlsp_file_worker(args):
    decl, path, file_idx, tmp_dir, ct_iso = args
    name = "hf_legal_vlsp2025"
    ct = datetime.fromisoformat(ct_iso)
    bound = ct.date()
    ext = f"{name}@{decl['revision'][:12]}"
    writer = ShardWriter(name, "hf", tmp_dir=tmp_dir, shard_prefix=f"f{file_idx:05d}")
    stats = {"rows": 0, "written": 0, "empty": 0, "invalid": 0, "era": {}}
    rel = path.relative_to(DATA_ROOT).as_posix()
    row_no = 0
    for batch in pq.ParquetFile(path).iter_batches(batch_size=1000):
        for r in batch.to_pylist():
            row_no += 1
            stats["rows"] += 1
            meta = r.get("metadata") or {}
            if isinstance(meta, str):
                meta = json.loads(meta)
            doc_id_key = str(meta.get("Id") or f"{path.name}:{row_no}")
            pub = parse_legal_date(meta.get("IssueDate"), bound)
            text = html_to_legal_text(r.get("doc_content") or "")
            rec = _make_record(
                name, f"{decl['repo']}#{doc_id_key}", text, ct,
                title=meta.get("DocName"), publish_date=pub, author=meta.get("OrganName"),
                raw_ref=f"{rel}#row={row_no}", extractor=ext,
                meta={"DocIdentity": meta.get("DocIdentity"), "Id": meta.get("Id")},
            )
            if rec is None:
                stats["empty"] += 1
            elif validate(rec):
                stats["invalid"] += 1
            else:
                writer.write(rec)
                stats["written"] += 1
                stats["era"][rec["era"]] = stats["era"].get(rec["era"], 0) + 1
    writer.close()
    return stats


def _export_vlsp2025(decl, workers):
    name = "hf_legal_vlsp2025"
    files = sorted((target_dir(decl) / "data").glob("*.parquet"))
    if not files:
        raise FileNotFoundError(f"No parquet files under {target_dir(decl)}")
    ct = lock_crawl_time(name).isoformat()
    tmp = staging_dir(name, "hf")
    totals = {"rows": 0, "written": 0, "empty": 0, "invalid": 0, "era": {}}
    try:
        with ProcessPoolExecutor(max_workers=min(workers, 4)) as pool:
            for i, st in enumerate(pool.map(_vlsp_file_worker, [(decl, f, i, tmp, ct) for i, f in enumerate(files)]), 1):
                for k in ("rows", "written", "empty", "invalid"):
                    totals[k] += st[k]
                for ek, ev in st["era"].items():
                    totals["era"][ek] = totals["era"].get(ek, 0) + ev
                print(f"{name}: {i}/{len(files)} files {totals}", flush=True)
        publish(tmp, name, "hf")
    except BaseException:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    return totals


EXPORTERS = {
    "hf_phapdien_tmquan": lambda d, w: _export_phapdien(d),
    "hf_legal_vohuutridung": lambda d, w: _export_vohuutridung(d),
    "hf_legal_th1nhng0": _export_th1nhng0,
    "hf_legal_vlsp2025": _export_vlsp2025,
}


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("names", nargs="*", default=sorted(EXPORTERS))
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args()
    decls = load_declarations()
    (REPORTS_DIR / "export").mkdir(parents=True, exist_ok=True)
    for name in args.names:
        if name not in EXPORTERS:
            parser.error(f"unknown legal dataset: {name}")
        stats = EXPORTERS[name](decls[name], max(1, min(args.workers, 4)))
        (REPORTS_DIR / "export" / f"{name}.json").write_text(json.dumps(stats, indent=1, ensure_ascii=False), encoding="utf-8")
        print(f"DONE {name}: {json.dumps(stats, ensure_ascii=False)}")


if __name__ == "__main__":
    main()
