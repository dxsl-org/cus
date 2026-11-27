"""
Documents the lab trains on, and where lab artifacts live.

A source is read from phase-6 output (processed/s4_clean) when it has been processed, else from canonical.
Run with LLM_DATA_ROOT=E:/llm-data: that root holds both s4_clean and the open datasets.
"""
import hashlib
import pathlib

import pyarrow.parquet as pq
import yaml

from pipeline.paths import DATA_ROOT, PROCESSED_DIR, canonical_source_dir

CONFIG_DIR = pathlib.Path(__file__).resolve().parent / "configs"
CLEAN_DIR = PROCESSED_DIR / "s4_clean"
LAB_DATA = DATA_ROOT / "lab"
TOKENIZER_PATH = LAB_DATA / "tokenizer" / "lab-vi-32k.json"
TOKENS_DIR = LAB_DATA / "tokens" / "lab-vi-32k"
RUNS_DIR = LAB_DATA / "runs"
EOT = "<|endoftext|>"
_COLUMNS = ["doc_id", "text", "usage_scope"]


def load_config(name):
    return yaml.safe_load((CONFIG_DIR / name).read_text(encoding="utf-8"))


def domain_map():
    """source_id -> lab domain (configs/domains.yaml)."""
    return {s: d for d, sources in load_config("domains.yaml").items() for s in sources}


def curated_sources():
    return sorted(p.name for p in CLEAN_DIR.iterdir() if p.is_dir())


def source_files(source_id):
    clean = CLEAN_DIR / source_id
    root = clean if clean.is_dir() else canonical_source_dir(source_id)
    if root is None:
        raise FileNotFoundError(f"no s4_clean or canonical data for {source_id}")
    return sorted(root.rglob("*.parquet"))


def iter_docs(source_id):
    """(doc_id, text) of every non-blocked document, in a fixed file/row order."""
    for path in source_files(source_id):
        yield from iter_file_docs(path)


def iter_file_docs(path):
    f = pq.ParquetFile(path)
    for i in range(f.num_row_groups):
        t = f.read_row_group(i, columns=_COLUMNS)
        for doc_id, text, scope in zip(*(t.column(c).to_pylist() for c in _COLUMNS)):
            if text and scope != "blocked":
                yield doc_id, text


def is_heldout(doc_id, fraction):
    """Stable split by doc_id: the same document is held out in every lab run and tokenizer."""
    h = int.from_bytes(hashlib.sha1(f"lab-heldout/v1/{doc_id}".encode()).digest()[:8], "big")
    return h < fraction * 2**64
