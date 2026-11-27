"""
Stage s3_minhash: near-duplicate removal (MinHash-LSH) within and across sources.

    uv run --group process python -m pipeline.process.stage_minhash [sources...] [--workers 4]

datatrove computes signatures (7-syllable shingles ~ FineWeb's 5 words; 14 bands x 8 hashes, i.e.
pairs above ~0.75 Jaccard) and LSH buckets, which write duplicate pairs (file, doc, file, doc).
Clustering is done here, not by datatrove's cluster step (a Python dict of every duplicate: too big
for ~10^8 documents): pairs become an undirected graph over only the documents that appear in a pair,
and scipy finds its connected components. In each component the document with the smallest global
index survives; files are ordered by SOURCE_PRIORITY, so dated articles from our own crawls win over
the same text in web dumps. One datatrove task reads exactly one s2 file (task i = file i), so a
document index in datatrove's output is its row number in that file.
"""
import argparse
import json
import re
import shutil
import sys
from collections import Counter, defaultdict

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
from datatrove.executor import LocalPipelineExecutor
from datatrove.pipeline.dedup.minhash import MinhashConfig, MinhashDedupBuckets, MinhashDedupSignature
from datatrove.pipeline.readers import ParquetReader
from datatrove.utils.text import TextNormConfig
from datatrove.utils.word_tokenizers import WordTokenizer
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import connected_components

from pipeline.ingest.canonical_writer import publish, staging_dir
from pipeline.paths import PROCESSED_DIR
from pipeline.process.stage_dedup_exact import SOURCE_PRIORITY

IN_STAGE, STAGE = "s2_exact", "s3_minhash"
# Diacritics stay: stripping them merges "ma/má/mà/mả/mã/mạ" and inflates false near-duplicate matches.
CONFIG = MinhashConfig(n_grams=7, num_buckets=14, hashes_per_bucket=8, seed=1,
                       norm_config=TextNormConfig(norm_unicode_diacritics=False))
WORK_DIR_NAME = ".s3_minhash_work"
SENTINEL = (1 << 32) - 1  # datatrove's marker for index matches; we build no index


class SyllableTokenizer(WordTokenizer):
    """Whitespace syllables: the unit every Vietnamese threshold in this pipeline counts."""
    _SENT = re.compile(r"[^.!?…\n]+[.!?…\n]*")

    def word_tokenize(self, text):
        return text.split()

    def sent_tokenize(self, text):
        return [s.strip() for s in self._SENT.findall(text) if s.strip()]

    def span_tokenize(self, text):
        spans, pos = [], 0
        for sent in self.sent_tokenize(text):
            start = text.index(sent, pos)
            spans.append((start, start + len(sent)))
            pos = start + len(sent)
        return spans


def _files(sources, root):
    """s2 files ordered by source priority: datatrove task i reads files[i]."""
    out = []
    for source in sorted(sources, key=lambda s: (SOURCE_PRIORITY.get(s, 0), s)):
        for origin_dir in sorted(d for d in (root / IN_STAGE / source).iterdir() if d.is_dir()):
            out += [(source, origin_dir.name, f) for f in sorted(origin_dir.glob("*.parquet"))]
    return out


def _execute(steps, tasks, workers, logs):
    """datatrove's default forkserver start method does not exist on Windows."""
    LocalPipelineExecutor(steps, tasks=tasks, workers=workers, logging_dir=str(logs), start_method="spawn").run()


def find_drops(pairs, offsets):
    """
    pairs: (n, 4) uint32 (file, doc, file, doc); offsets: first global index of each file.
    Returns (dropped global indexes, keeper global index of each), keeper = smallest index in its component.
    """
    pairs = pairs[pairs[:, 0] != SENTINEL]
    if not len(pairs):
        return np.array([], np.int64), np.array([], np.int64)
    a = offsets[pairs[:, 0].astype(np.int64)] + pairs[:, 1]
    b = offsets[pairs[:, 2].astype(np.int64)] + pairs[:, 3]
    nodes, local = np.unique(np.concatenate([a, b]), return_inverse=True)  # only documents in a pair
    edges = local.reshape(2, -1)
    graph = coo_matrix((np.ones(edges.shape[1], np.int8), (edges[0], edges[1])), shape=(len(nodes), len(nodes)))
    _, label = connected_components(graph, directed=False)
    keeper = np.full(label.max() + 1, np.iinfo(np.int64).max)
    np.minimum.at(keeper, label, nodes)  # nodes are sorted, so this is the earliest file/row
    keep_of_node = keeper[label]
    dropped = nodes != keep_of_node
    return nodes[dropped], keep_of_node[dropped]


def _read_pairs(folder):
    parts = [np.fromfile(p, dtype="<u4").reshape(-1, 4) for p in sorted(folder.glob("*.dups"))]
    return np.concatenate(parts) if parts else np.zeros((0, 4), np.uint32)


def run(sources, workers, root=PROCESSED_DIR):
    files, work = _files(sources, root), root / WORK_DIR_NAME
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)
    paths_file = work / "paths.txt"
    paths_file.write_text("\n".join(p.relative_to(root / IN_STAGE).as_posix() for _, _, p in files) + "\n",
                          encoding="utf-8")
    reader = ParquetReader(str(root / IN_STAGE), paths_file=str(paths_file), text_key="text", id_key="doc_id",
                           read_metadata=False)
    _execute([reader, MinhashDedupSignature(str(work / "sigs"), config=CONFIG, language=SyllableTokenizer())],
             len(files), workers, work / "logs1")
    _execute([MinhashDedupBuckets(str(work / "sigs"), str(work / "buckets"), config=CONFIG)],
             CONFIG.num_buckets, workers, work / "logs2")

    counts = np.array([pq.ParquetFile(p).metadata.num_rows for _, _, p in files], np.int64)
    offsets = np.concatenate([[0], np.cumsum(counts)[:-1]])
    dropped, kept_by = find_drops(_read_pairs(work / "buckets"), offsets)
    drop_file = np.searchsorted(offsets, dropped, side="right") - 1
    keep_file = np.searchsorted(offsets, kept_by, side="right") - 1
    drop = defaultdict(list)
    for f, g in zip(drop_file, dropped):
        drop[int(f)].append(int(g - offsets[f]))
    matrix = Counter((files[d][0], files[k][0]) for d, k in zip(drop_file, keep_file))

    staging, per_source = {}, defaultdict(lambda: {"docs_in": 0, "docs_out": 0})
    for i, (source, origin, path) in enumerate(files):
        if (source, origin) not in staging:
            staging[(source, origin)] = staging_dir(source, origin, root / STAGE)
        table = pq.read_table(path)
        per_source[source]["docs_in"] += table.num_rows
        if drop.get(i):
            keep = np.ones(table.num_rows, dtype=bool)
            keep[drop[i]] = False
            table = table.filter(pa.array(keep))
        per_source[source]["docs_out"] += table.num_rows
        pq.write_table(table, staging[(source, origin)] / path.name, compression="zstd", row_group_size=2000)
    for (source, origin), tmp in staging.items():
        publish(tmp, source, origin, root / STAGE)
    shutil.rmtree(work, ignore_errors=True)
    stats = {"stage": STAGE, "config": str(CONFIG), "dropped": int(len(dropped)),
             "sources": dict(sorted(per_source.items())),
             "dropped_to_kept": {f"{d} -> {k}": n for (d, k), n in sorted(matrix.items())}}
    (root / STAGE / "stats.json").write_text(json.dumps(stats, indent=1), encoding="utf-8")
    return stats


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("sources", nargs="*")
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args()
    sources = args.sources or sorted(d.name for d in (PROCESSED_DIR / IN_STAGE).iterdir() if d.is_dir())
    print(json.dumps(run(sources, args.workers), indent=1, ensure_ascii=False))


if __name__ == "__main__":
    main()
