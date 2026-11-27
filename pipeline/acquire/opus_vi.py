"""
Acquire and export OPUS en-vi corpora (Vietnamese side) to canonical DocumentRecords:
- TED2020 v1 (~23 MB zip, pre_llm)
- QED v2.0a (~18.5 MB zip, pre_llm)
- OpenSubtitles v2024 (~908 MB zip, dialogue)

    uv run python -m pipeline.acquire.opus_vi download [corpora...]
    uv run python -m pipeline.acquire.opus_vi export [corpora...]
"""
import argparse
import hashlib
import json
import re
import subprocess
import sys
import unicodedata
import zipfile
from datetime import datetime
from pathlib import Path
from urllib.request import Request, urlopen

from pipeline.ingest.canonical_writer import ShardWriter
from pipeline.paths import DATA_ROOT, RAW_DIR, REPORTS_DIR
from pipeline.schema.document_record import era_for, make_doc_id, text_sha256, validate

SOURCE_ID = "opus_vi"
OPUS_API = "https://opus.nlpl.eu/opusapi/?source=en&target=vi&preprocessing=moses&version=latest"

CORPORA = {
    "TED2020": {
        "version": "v1",
        "fallback_url": "https://object.pouta.csc.fi/OPUS-TED2020/v1/moses/en-vi.txt.zip",
        "crawl_time": "2020-12-31T23:59:59",
        "corpus_license": "cc-by-nc-nd-4.0",
    },
    "QED": {
        "version": "v2.0a",
        "fallback_url": "https://object.pouta.csc.fi/OPUS-QED/v2.0a/moses/en-vi.txt.zip",
        "crawl_time": "2020-06-30T23:59:59",
        "corpus_license": "cc-by-sa-4.0",
    },
    "OpenSubtitles": {
        "version": "v2024",
        "fallback_url": "https://object.pouta.csc.fi/OPUS-OpenSubtitles/v2024/moses/en-vi.txt.zip",
        "crawl_time": "2024-12-31T23:59:59",
        "corpus_license": "unknown",
    },
}

_CTRL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
_HTML_TAG_RE = re.compile(r"</?(?:i|b|u|font|br)\b[^>]*>", re.IGNORECASE)
_HAS_LETTER_RE = re.compile(r"[^\W\d_]", re.UNICODE)


def _nfc(text):
    return text if unicodedata.is_normalized("NFC", text) else unicodedata.normalize("NFC", text)


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(8 * 2**20):
            h.update(chunk)
    return h.hexdigest()


def resolve_urls():
    """Query OPUS API for latest moses en-vi zip URLs, falling back to pinned URLs."""
    urls = {k: v["fallback_url"] for k, v in CORPORA.items()}
    try:
        req = Request(OPUS_API, headers={"User-Agent": "LLM-Data-Acquire/1.0"})
        with urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        for entry in data.get("corpora", []):
            name = entry.get("corpus")
            if name in urls and entry.get("url"):
                urls[name] = entry["url"]
    except Exception:
        pass
    return urls


def clean_opus_line(line):
    line = _CTRL_RE.sub("", _HTML_TAG_RE.sub("", line))
    line = re.sub(r"\s+", " ", line).strip()
    if len(line) < 2 or not _HAS_LETTER_RE.search(line):
        return ""
    return _nfc(line)


def group_lines_to_docs(lines, max_lines=40, max_chars=2200, min_chars=120):
    """Yield (start_line_no, end_line_no, grouped_text) chunks from an iterable of lines."""
    buf, chars, start_no = [], 0, 1
    for idx, raw in enumerate(lines, 1):
        cleaned = clean_opus_line(raw)
        if not cleaned:
            continue
        if not buf:
            start_no = idx
        buf.append(cleaned)
        chars += len(cleaned) + 1
        if len(buf) >= max_lines or chars >= max_chars:
            text = "\n".join(buf)
            if len(text) >= min_chars:
                yield start_no, idx, text
            buf, chars = [], 0
    if buf:
        text = "\n".join(buf)
        if len(text) >= min_chars:
            yield start_no, idx, text  # type: ignore[possibly-undefined]


def download(selected):
    raw_dir = RAW_DIR / "open_datasets" / SOURCE_ID
    raw_dir.mkdir(parents=True, exist_ok=True)
    urls = resolve_urls()
    lock_path = raw_dir / "lock.json"
    lock = json.loads(lock_path.read_text(encoding="utf-8")) if lock_path.exists() else {"corpora": {}}
    for name in selected:
        zip_path = raw_dir / f"{name}-en-vi.txt.zip"
        if not zip_path.exists():
            part = zip_path.with_suffix(".zip.part")
            subprocess.run(
                ["curl", "-4", "-fsSL", "--retry", "15", "--retry-all-errors", "--retry-delay", "15",
                 "-C", "-", "-o", str(part), urls[name]],
                check=True,
            )
            part.rename(zip_path)
        sha = _sha256(zip_path)
        lock["corpora"][name] = {
            "url": urls[name],
            "file": zip_path.name,
            "size": zip_path.stat().st_size,
            "sha256": sha,
            "version": CORPORA[name]["version"],
        }
        lock_path.write_text(json.dumps(lock, indent=1), encoding="utf-8")
        print(f"opus_vi download {name}: {zip_path.stat().st_size / 1048576:.1f} MB sha256={sha[:12]}", flush=True)


def _iter_vi_lines(zip_path):
    with zipfile.ZipFile(zip_path) as zf:
        vi_member = next((n for n in zf.namelist() if n.endswith(".vi")), None)
        if not vi_member:
            raise FileNotFoundError(f"No .vi file inside {zip_path}")
        with zf.open(vi_member) as fh:
            for raw in fh:
                yield raw.decode("utf-8", errors="replace")


def export(selected):
    raw_dir = RAW_DIR / "open_datasets" / SOURCE_ID
    writer = ShardWriter(SOURCE_ID, "hf")
    stats = {"corpora": {}, "written": 0, "invalid": 0, "era": {}}
    try:
        for name in selected:
            cfg = CORPORA[name]
            zip_path = raw_dir / f"{name}-en-vi.txt.zip"
            if not zip_path.exists():
                raise FileNotFoundError(f"Missing {zip_path}; run download first")
            rel = zip_path.relative_to(DATA_ROOT).as_posix()
            ct = datetime.fromisoformat(cfg["crawl_time"])
            era = era_for(None, ct)
            c_stats = {"written": 0, "invalid": 0, "era": era}
            for start_ln, end_ln, text in group_lines_to_docs(_iter_vi_lines(zip_path)):
                rec = {
                    "doc_id": make_doc_id(SOURCE_ID, f"opus/{name}/{cfg['version']}#L{start_ln}-{end_ln}"),
                    "source_id": SOURCE_ID,
                    "source_type": "open_dataset",
                    "url": f"https://opus.nlpl.eu/{name}-{cfg['version']}.php",
                    "title": None,
                    "text": text,
                    "lang": None,
                    "lang_score": None,
                    "publish_date": None,
                    "publish_date_source": "none",
                    "crawl_time": ct,
                    "era": era,
                    "author": None,
                    "category": name,
                    "tags": [],
                    "raw_ref": f"{rel}#L{start_ln}-{end_ln}",
                    "text_sha256": text_sha256(text),
                    "license": "unknown",
                    "usage_scope": "train_ok",
                    "extractor": f"opus_vi@{name}-{cfg['version']}",
                    "meta": json.dumps({
                        "corpus": name,
                        "version": cfg["version"],
                        "corpus_license": cfg["corpus_license"],
                        "crawl_time_inferred": True,
                        "lines": [start_ln, end_ln],
                    }, ensure_ascii=False),
                }
                if validate(rec):
                    c_stats["invalid"] += 1
                    stats["invalid"] += 1
                else:
                    writer.write(rec)
                    c_stats["written"] += 1
                    stats["written"] += 1
                    stats["era"][era] = stats["era"].get(era, 0) + 1
            stats["corpora"][name] = c_stats
            print(f"opus_vi export {name}: {c_stats}", flush=True)
        writer.commit()
    except BaseException:
        writer.abort()
        raise
    (REPORTS_DIR / "export").mkdir(parents=True, exist_ok=True)
    (REPORTS_DIR / "export" / f"{SOURCE_ID}.json").write_text(json.dumps(stats, indent=1, ensure_ascii=False), encoding="utf-8")
    return stats


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["download", "export"])
    parser.add_argument("corpora", nargs="*", default=list(CORPORA))
    args = parser.parse_args()
    unknown = set(args.corpora) - set(CORPORA)
    if unknown:
        parser.error(f"unknown corpora: {sorted(unknown)}")
    if args.command == "download":
        download(args.corpora)
    else:
        print(json.dumps(export(args.corpora), ensure_ascii=False))


if __name__ == "__main__":
    main()
