"""
Tokenize the lab corpus into flat uint16 shards, one train and one held-out file per source.

  LLM_DATA_ROOT=E:/llm-data uv run --group lab python -m lab.data_prep [--redo SOURCE ...]

Layout under TOKENS_DIR: <source>.train.bin, <source>.heldout.bin (documents joined by EOT), <source>.json
(counts, eval sets filtered against, documents dropped as contaminated and the eval items they hit),
manifest.json (all sources, per-domain totals, tokenizer sha256). A source whose .json exists is skipped unless
named in --redo, so an interrupted run resumes; files are written to .tmp and renamed only when complete.
"""
import argparse
import hashlib
import json
import sys
import time
from collections import Counter

import numpy as np
from tokenizers import Tokenizer

from lab import corpus
from pipeline.process.decontam import EvalIndex

BATCH_DOCS = 2000


def planned_sources():
    """(source_id, max_tokens or None) from configs/data-mix.yaml, with `curated` expanded."""
    plan = []
    for name, spec in corpus.load_config("data-mix.yaml")["sources"].items():
        cap = spec.get("max_tokens")
        names = corpus.curated_sources() if name == "curated" else [name]
        plan += [(s, int(float(cap)) if cap else None) for s in names]
    missing = sorted(s for s, _ in plan if s not in corpus.domain_map())
    if missing:
        raise SystemExit(f"add these sources to lab/configs/domains.yaml first: {missing}")
    return plan


def tokenize_source(tok, index, source_id, cap, fraction):
    """Documents matching a frozen eval item (pipeline.process.decontam) are dropped: open datasets and books
    are read from canonical and never went through stage s4, and s4_clean predates newer eval sets."""
    eot = tok.token_to_id(corpus.EOT)
    paths = {k: corpus.TOKENS_DIR / f"{source_id}.{k}.bin" for k in ("train", "heldout")}
    tmps = {k: p.with_suffix(".tmp") for k, p in paths.items()}
    counts = {"train_tokens": 0, "heldout_tokens": 0, "train_docs": 0, "heldout_docs": 0, "bytes": 0,
              "evalsets": list(index.evalsets), "decontam_dropped": 0, "eval_items_hit": Counter()}
    files = {k: open(t, "wb") for k, t in tmps.items()}
    try:
        batch = []
        for doc in corpus.iter_docs(source_id):
            hits = index.matches(doc[1])
            if hits:
                counts["decontam_dropped"] += 1
                counts["eval_items_hit"].update(hits)
                continue
            batch.append(doc)
            if len(batch) == BATCH_DOCS:
                _write_batch(tok, batch, eot, fraction, files, counts)
                batch = []
                if cap and counts["train_tokens"] >= cap:
                    break
        if batch:
            _write_batch(tok, batch, eot, fraction, files, counts)
    finally:
        for f in files.values():
            f.close()
    for k in paths:
        tmps[k].replace(paths[k])
    return counts


def _write_batch(tok, batch, eot, fraction, files, counts):
    encodings = tok.encode_batch([text for _, text in batch])
    parts = {"train": [], "heldout": []}
    for (doc_id, text), enc in zip(batch, encodings):
        split = "heldout" if corpus.is_heldout(doc_id, fraction) else "train"
        parts[split].append(np.asarray(enc.ids + [eot], dtype=np.uint16))
        counts[f"{split}_docs"] += 1
        counts["bytes"] += len(text.encode())
    for split, arrays in parts.items():
        if arrays:
            joined = np.concatenate(arrays)
            joined.tofile(files[split])
            counts[f"{split}_tokens"] += int(joined.size)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--redo", nargs="+", default=[], metavar="SOURCE",
                        help="re-tokenize these sources (e.g. after lab.decontam_scan found eval items in them)")
    args = parser.parse_args()
    if not corpus.TOKENIZER_PATH.exists():
        raise SystemExit(f"train the tokenizer first: {corpus.TOKENIZER_PATH} missing")
    tok = Tokenizer.from_file(str(corpus.TOKENIZER_PATH))
    index = EvalIndex()
    fraction = corpus.load_config("data-mix.yaml")["heldout_fraction"]
    corpus.TOKENS_DIR.mkdir(parents=True, exist_ok=True)
    domains = corpus.domain_map()
    sources = {}
    for source_id, cap in planned_sources():
        stats_path = corpus.TOKENS_DIR / f"{source_id}.json"
        if source_id in args.redo or not stats_path.exists():
            start = time.time()
            counts = tokenize_source(tok, index, source_id, cap, fraction)
            counts["eval_items_hit"] = dict(sorted(counts["eval_items_hit"].items()))
            counts["seconds"] = round(time.time() - start)
            stats_path.write_text(json.dumps(counts), encoding="utf-8")
        sources[source_id] = json.loads(stats_path.read_text(encoding="utf-8"))
        print(source_id, {k: v for k, v in sources[source_id].items() if k != "eval_items_hit"},
              file=sys.stderr, flush=True)
    by_domain = {}
    for source_id, c in sources.items():
        d = by_domain.setdefault(domains[source_id], {"train_tokens": 0, "heldout_tokens": 0})
        d["train_tokens"] += c["train_tokens"]
        d["heldout_tokens"] += c["heldout_tokens"]
    manifest = {"tokenizer": corpus.TOKENIZER_PATH.name,
                "tokenizer_sha256": hashlib.sha256(corpus.TOKENIZER_PATH.read_bytes()).hexdigest(),
                "heldout_fraction": fraction, "domains": by_domain, "sources": sources}
    (corpus.TOKENS_DIR / "manifest.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    total = sum(c["train_tokens"] for c in sources.values())
    print(f"done: {total / 1e9:.2f}B train tokens over {len(sources)} sources")


if __name__ == "__main__":
    main()
