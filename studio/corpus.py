"""
Corpus mode: read-only views of pretrain Parquet (canonical exports and processed stages).
Nothing is copied into SQLite. A sample opens a few random row groups (pyarrow); search and
single-document reads run in DuckDB over the source's files, a batch at a time, and stop at the
hit limit or the time budget so a 100 GB open-data source never blocks the browser.
A document is addressed by {file, row}: its row number within that Parquet file.
"""
import json
import os
import random
import re
import threading
import time
from functools import cache
from pathlib import Path

import duckdb
import pyarrow.parquet as pq

from pipeline.paths import CANONICAL_ROOTS, PROCESSED_DIR
from pipeline.schema.document_record import ERAS
from studio import db

STAGES = ("canonical", "s1_filter", "s1_rejected", "s2_exact", "s3_minhash", "s4_clean", "final")
TIERS = ("tier_a", "tier_b", "tier_c")
SHOW = ["doc_id", "source_id", "url", "title", "text", "lang", "publish_date", "era", "usage_scope"]
PREVIEW = 3000
DUCK_THREADS = max(4, (os.cpu_count() or 8) // 2)  # half the cores: crawls and labelling keep running beside it
BATCH_BYTES = 512 * 2**20
SEARCH_BUDGET_S = 20


def _roots(stage, tier=None):
    if stage == "canonical":
        return list(CANONICAL_ROOTS)
    if stage not in STAGES:
        raise ValueError(f"không có tầng {stage}")
    if stage == "final":
        return [PROCESSED_DIR / "final" / t for t in ((tier,) if tier else TIERS)]
    return [PROCESSED_DIR / stage]


def overview():
    """Sources present per stage, and the final stage's document/syllable table when s5 has run."""
    stages = {}
    for stage in STAGES:
        names = {d.name for root in _roots(stage) if root.is_dir() for d in root.iterdir() if d.is_dir()}
        if names:
            stages[stage] = sorted(names)
    stats = PROCESSED_DIR / "final" / "stats.json"
    return {"stages": stages, "tiers": list(TIERS), "eras": sorted(ERAS),
            "final_stats": json.loads(stats.read_text(encoding="utf-8")) if stats.is_file() else None}


def files(stage, source, tier=None):
    out = []
    for root in _roots(stage, tier):
        if (root / source).is_dir():
            out += sorted((root / source).rglob("*.parquet"))
        if out and stage == "canonical":  # first root holding the source wins (pipeline.paths)
            break
    if not out:
        raise FileNotFoundError(f"không có dữ liệu {stage}/{source}")
    return out


def _allowed(path):
    path = Path(path).resolve()
    roots = [*CANONICAL_ROOTS, PROCESSED_DIR]
    if path.suffix != ".parquet" or not any(path.is_relative_to(Path(r).resolve()) for r in roots):
        raise ValueError("đường dẫn ngoài vùng corpus")
    return path


def _columns(path):
    names = set(pq.ParquetFile(path).schema_arrow.names)
    return [c for c in [*SHOW, "q_edu", "reason", "text_head"] if c in names]


def _duck():
    # Background crawls and labelling share the machine: cap DuckDB's threads.
    con = duckdb.connect(config={"threads": DUCK_THREADS})
    con.execute("SET enable_progress_bar = false")
    return con


@cache
def _variants():
    """Folded letter -> every Vietnamese/Latin letter (both cases) that folds to it, e.g. d -> dDđĐ."""
    out = {}
    for cp in [*range(0x41, 0x5B), *range(0x61, 0x7B), *range(0xC0, 0x1EFA)]:
        ch = chr(cp)
        key = db.fold(ch)
        if ch.isalpha() and len(key) == 1 and key.isascii():
            out.setdefault(key, set()).update({ch, ch.upper(), ch.lower()})
    return out


def _pattern(needle):
    """RE2 regex matching `needle` with any accents or case; runs ~7x faster in DuckDB than folding the text."""
    variants = _variants()
    parts = []
    for ch in needle:
        if ch.isspace():
            parts.append(r"\s+")
        elif ch in variants:
            parts.append("[" + "".join(sorted(variants[ch])) + "]")
        else:
            parts.append(re.escape(ch))
    return "".join(parts)


def _row(path, row, rec, full=False):
    text = rec.get("text") or rec.get("text_head") or ""
    rec = {**rec, "text": text if full else text[:PREVIEW], "chars": len(text), "ref": {"file": str(path), "row": row}}
    rec.pop("text_head", None)
    return {k: (str(v) if k == "publish_date" and v is not None else v) for k, v in rec.items()}


def sample(stage, source, tier=None, era=None, n=20, seed=None):
    """`n` documents from random row groups; a given seed reproduces the same sample."""
    rng = random.Random(seed)
    paths = files(stage, source, tier)
    out, tries = [], 0
    while len(out) < n and tries < 4 * n:
        tries += 1
        path = rng.choice(paths)
        meta = pq.ParquetFile(path)
        group = rng.randrange(meta.num_row_groups)
        offset = sum(meta.metadata.row_group(g).num_rows for g in range(group))
        rows = meta.read_row_group(group, columns=_columns(path)).to_pylist()
        picks = [i for i, r in enumerate(rows) if not era or r.get("era") == era]
        for idx in rng.sample(picks, min(len(picks), max(1, n // 4))):
            if len(out) < n:
                out.append(_row(path, offset + idx, rows[idx]))
    return out


def document(ref):
    path = _allowed(ref["file"])
    cols = _columns(path)
    with _duck() as con:
        row = con.execute(f"SELECT {', '.join(cols)} FROM read_parquet(?, file_row_number = true) "
                          "WHERE file_row_number = ?", [str(path), int(ref["row"])]).fetchone()
    if row is None:
        raise FileNotFoundError("không thấy tài liệu")
    return _row(path, int(ref["row"]), dict(zip(cols, row)), full=True)


def _batches(paths):
    """Groups of files of about BATCH_BYTES, so one batch of 200 MB open-data shards cannot outlast the budget."""
    batch, size = [], 0
    for p in paths:
        batch.append(str(p))
        size += p.stat().st_size
        if size >= BATCH_BYTES:
            yield batch
            batch, size = [], 0
    if batch:
        yield batch


def search(stage, source, query, tier=None, limit=20, budget_s=SEARCH_BUDGET_S):
    """
    Accent-insensitive search over a whole source, batch by batch. The scan is interrupted when the
    time budget runs out; `matches` counts every hit in the files finished.
    """
    needle = " ".join(db.fold(query).split())
    if len(needle) < 3:
        raise ValueError("từ khoá cần ít nhất 3 ký tự")
    pattern = _pattern(needle)
    paths = files(stage, source, tier)
    cols = _columns(paths[0])
    text_col = "text" if "text" in cols else "text_head"
    scan = "read_parquet(?, filename = true, file_row_number = true, union_by_name = true)"
    hits, matches, scanned = [], 0, 0
    with _duck() as con:
        timer = threading.Timer(budget_s, con.interrupt)
        timer.start()
        try:
            for batch in _batches(paths):
                if len(hits) < limit:
                    rows = con.execute(f"SELECT {', '.join(cols)}, filename, file_row_number, count(*) OVER () "
                                       f"FROM {scan} WHERE regexp_matches({text_col}, ?) LIMIT ?",
                                       [batch, pattern, limit - len(hits)]).fetchall()
                    matches += rows[0][-1] if rows else 0
                    for r in rows:
                        rec = dict(zip(cols, r))
                        text = rec.get("text") or rec.get("text_head") or ""
                        found = re.search(pattern, text)
                        at, end = (found.start(), found.end()) if found else (0, 0)
                        hits.append({**_row(r[len(cols)], r[len(cols) + 1], rec),
                                     "snippet": text[max(0, at - 300): end + 500]})
                else:
                    matches += con.execute(f"SELECT count(*) FROM {scan} WHERE regexp_matches({text_col}, ?)",
                                           [batch, pattern]).fetchone()[0]
                scanned += len(batch)
        except duckdb.InterruptException:
            pass  # budget spent: report the files finished so far
        finally:
            timer.cancel()
    return {"hits": hits, "matches": matches, "files_scanned": scanned, "files": len(paths),
            "complete": scanned == len(paths)}

