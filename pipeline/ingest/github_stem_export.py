"""
Export cloned Vietnamese STEM GitHub repositories into canonical DocumentRecords:
- vnoi_wiki (VNOI-Admin/vnoi_wiki)
- mlcb_tiepvupsu (tiepvupsu/tiepvupsu.github.io)
- d2l_vn (aivivn/d2l-vn)

    uv run python -m pipeline.ingest.github_stem_export [source_ids...]
"""
import argparse
import json
import re
import sys
import unicodedata
from datetime import date, datetime, timezone
from pathlib import Path

import yaml

from pipeline.ingest.canonical_writer import ShardWriter
from pipeline.paths import RAW_DIR, REPORTS_DIR
from pipeline.schema.document_record import era_for, make_doc_id, text_sha256, validate

REPOS = {
    "vnoi_wiki": {
        "license": "agpl-3.0",
        "suffixes": (".md",),
        "skip_names": {"readme.md", "contributing.md", "security.md", "code_of_conduct.md"},
        "skip_dirs": {".git", ".github", "node_modules"},
        "base_url": "https://wiki.vnoi.info/",
    },
    "mlcb_tiepvupsu": {
        "license": "unknown",
        "suffixes": (".md", ".markdown"),
        "skip_names": {"buymeacoffee.md", "copyrights.md", "about.md", "categories.md", "index.md", "ebook_old.md"},
        "skip_dirs": {".git", "_site", "_includes", "_layouts"},
        "base_url": "https://machinelearningcoban.com/",
    },
    "d2l_vn": {
        "license": "unknown",
        "suffixes": (".md",),
        "skip_names": {"readme.md", "contributing.md", "style_guide.md", "acknowledgement.md", "info.md", "index.md"},
        "skip_dirs": {".git", "_build", "img", "static"},
        "base_url": "https://d2l.aivivn.com/",
    },
}

_FRONTMATTER_RE = re.compile(r"\A---\s*\r?\n(.*?)\r?\n---\s*(?:\r?\n|$)", re.DOTALL)
_HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
_LIQUID_RE = re.compile(r"\{%.*?%\}", re.DOTALL)
_LIQUID_VAR_RE = re.compile(r"\{\{\s*(?:site|page)\.[^}]+\}\}")
_H1_RE = re.compile(r"^#\s+(.+?)\s*$", re.MULTILINE)
_FILENAME_DATE_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})-")
_BLANK_RE = re.compile(r"\n{3,}")
_VI_ACCENT_RE = re.compile(r"[àáảãạăằắẳẵặâầấẩẫậđèéẻẽẹêềếểễệìíỉĩịòóỏõọôồốổỗộơờớởỡợùúủũụưừứửữựỳýỷỹỵ]")


def _nfc(text):
    return text if unicodedata.is_normalized("NFC", text) else unicodedata.normalize("NFC", text)


def parse_markdown_doc(raw_text, rel_path=""):
    """Strip Jekyll/YAML frontmatter and Liquid/HTML noise; return (meta_dict, clean_text)."""
    meta = {}
    body = raw_text.lstrip("\ufeff")
    if m := _FRONTMATTER_RE.match(body):
        try:
            loaded = yaml.safe_load(m.group(1))
            if isinstance(loaded, dict):
                meta = loaded
        except yaml.YAMLError:
            pass
        body = body[m.end():]
    body = _HTML_COMMENT_RE.sub("", body)
    body = _LIQUID_RE.sub("", body)
    body = _LIQUID_VAR_RE.sub("", body)
    # Remove HTML wrapper tags while preserving markdown, math ($...$), and fenced code.
    body = re.sub(r"</?(?:div|figure|figcaption|center|span)[^>]*>", "", body, flags=re.IGNORECASE)
    lines = [line.rstrip() for line in body.splitlines()]
    text = _nfc(_BLANK_RE.sub("\n\n", "\n".join(lines))).strip()

    title = meta.get("title")
    if not title and (hm := _H1_RE.search(text)):
        title = hm.group(1)
    title = _nfc(str(title)).strip() if title else None

    if "date" not in meta and (dm := _FILENAME_DATE_RE.match(Path(rel_path).name)):
        meta["_filename_date"] = f"{dm[1]}-{dm[2]}-{dm[3]}"
    return meta, title, text


def _parse_pub_date(meta, bound):
    raw = meta.get("date") or meta.get("_filename_date")
    if not raw:
        return None
    if isinstance(raw, datetime):
        d = raw.date()
    elif isinstance(raw, date):
        d = raw
    else:
        m = re.search(r"(\d{4})-(\d{1,2})-(\d{1,2})", str(raw))
        if not m:
            return None
        try:
            d = date(int(m[1]), int(m[2]), int(m[3]))
        except ValueError:
            return None
    return d if date(2000, 1, 1) <= d <= bound else None


def export_repo(source_id):
    cfg = REPOS[source_id]
    base = RAW_DIR / "github_stem" / source_id
    lock_path = RAW_DIR / "github_stem" / f"{source_id}.lock.json"
    if not base.is_dir():
        raise FileNotFoundError(f"Repo not cloned: {base}")
    lock = json.loads(lock_path.read_text(encoding="utf-8-sig")) if lock_path.exists() else {}
    commit_sha = lock.get("head", "unknown")
    commit_dt_raw = lock.get("commit_date")
    crawl_time = (datetime.fromisoformat(commit_dt_raw).astimezone(timezone.utc).replace(tzinfo=None)
                  if commit_dt_raw else datetime.now(timezone.utc).replace(tzinfo=None))
    bound = crawl_time.date()
    extractor = f"github_stem@{commit_sha[:12]}"

    writer = ShardWriter(source_id, "dump")
    stats = {"files": 0, "written": 0, "skipped_short": 0, "skipped_non_vi": 0, "invalid": 0, "era": {}}
    try:
        for path in sorted(base.rglob("*")):
            if not path.is_file() or path.suffix.lower() not in cfg["suffixes"]:
                continue
            rel_parts = path.relative_to(base).parts
            if any(part in cfg["skip_dirs"] for part in rel_parts):
                continue
            if path.name.lower() in cfg["skip_names"]:
                continue
            stats["files"] += 1
            rel = path.relative_to(base).as_posix()
            raw = path.read_text(encoding="utf-8", errors="replace")
            meta, title, text = parse_markdown_doc(raw, rel)
            if len(text) < 200:
                stats["skipped_short"] += 1
                continue
            if len(_VI_ACCENT_RE.findall(text.lower())) < 5:
                stats["skipped_non_vi"] += 1
                continue
            pub = _parse_pub_date(meta, bound)
            tags = meta.get("tags") or meta.get("categories") or []
            if isinstance(tags, str):
                tags = [t.strip() for t in tags.split(",") if t.strip()]
            elif isinstance(tags, list):
                tags = [_nfc(str(t)).strip() for t in tags if t]
            else:
                tags = []
            rec = {
                "doc_id": make_doc_id(source_id, f"{lock.get('url', source_id)}#{rel}"),
                "source_id": source_id,
                "source_type": "education",
                "url": cfg["base_url"] + rel.removesuffix(".md").removesuffix(".markdown"),
                "title": title,
                "text": text,
                "lang": None,
                "lang_score": None,
                "publish_date": pub,
                "publish_date_source": "meta:frontmatter" if pub else "none",
                "crawl_time": crawl_time,
                "era": era_for(pub, crawl_time),
                "author": _nfc(str(meta["author"])).strip() if meta.get("author") else None,
                "category": _nfc(rel_parts[0]) if len(rel_parts) > 1 else None,
                "tags": tags,
                "raw_ref": f"raw/github_stem/{source_id}/{rel}",
                "text_sha256": text_sha256(text),
                "license": cfg["license"],
                "usage_scope": "train_ok",
                "extractor": extractor,
                "meta": json.dumps({"repo_path": rel, "commit": commit_sha}, ensure_ascii=False),
            }
            if validate(rec):
                stats["invalid"] += 1
            else:
                writer.write(rec)
                stats["written"] += 1
                stats["era"][rec["era"]] = stats["era"].get(rec["era"], 0) + 1
        writer.commit()
    except BaseException:
        writer.abort()
        raise
    (REPORTS_DIR / "export").mkdir(parents=True, exist_ok=True)
    (REPORTS_DIR / "export" / f"{source_id}.json").write_text(json.dumps(stats, indent=1, ensure_ascii=False), encoding="utf-8")
    return stats


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("sources", nargs="*", default=list(REPOS))
    args = parser.parse_args()
    for sid in args.sources:
        if sid not in REPOS:
            parser.error(f"unknown source: {sid}")
        st = export_repo(sid)
        print(f"DONE {sid}: {json.dumps(st, ensure_ascii=False)}")


if __name__ == "__main__":
    main()
