"""
Stage s2_exact: drop exact duplicate texts (same text_sha256 after s1 normalization) within and
across sources, keeping one copy per text.

    uv run python -m pipeline.process.stage_dedup_exact [sources...] [--workers 8]

Which copy survives: lowest SOURCE_PRIORITY (our own crawls carry dates and metadata; open web
dumps come last), then smallest doc_id. Sized for ~10^8 documents without holding them in memory:
  A. each s1 file -> (sha128, rank, doc_id, file, row) split into BUCKETS by the first hex digit;
  B. per bucket: sort, mark every non-first row of a sha run as dropped;
  C. per s1 file: copy it without its dropped rows into s2_exact/<source>/<origin>/.
stats.json (no timestamps): docs in/out per source and dropped-source -> kept-source counts,
i.e. how much each source repeats the others. Worker functions receive every path as an
argument because spawned workers re-import this module.
"""
import argparse
import json
import shutil
import sys
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor

import numpy as np
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

from pipeline.ingest.canonical_writer import publish, staging_dir
from pipeline.paths import PROCESSED_DIR

IN_STAGE, STAGE = "s1_filter", "s2_exact"
BUCKETS = "0123456789abcdef"
# Lower survives. Unlisted sources (our crawls) rank 0.
SOURCE_PRIORITY = {"vi_wikipedia_org": 1, "fineweb2_vie": 2, "culturax_vi": 3, "hplt2_vie": 4}
WORK_DIR_NAME = ".s2_exact_work"
_EMPTY = {"file": pa.int32(), "row": pa.int32(), "kept_file": pa.int32()}


def _files(sources, root):
    out = []
    for source in sources:
        for origin_dir in sorted(d for d in (root / IN_STAGE / source).iterdir() if d.is_dir()):
            out += [(source, origin_dir.name, f) for f in sorted(origin_dir.glob("*.parquet"))]
    return out


def phase_a(args):
    file_id, (source, _, path), work = args
    table = pq.read_table(path, columns=["doc_id", "text_sha256"])
    n = table.num_rows
    keys = pa.table({"sha": pc.utf8_slice_codeunits(table["text_sha256"], 0, 32), "doc_id": table["doc_id"],
                     "rank": pa.array(np.full(n, SOURCE_PRIORITY.get(source, 0), np.int8)),
                     "file": pa.array(np.full(n, file_id, np.int32)), "row": pa.array(np.arange(n, dtype=np.int32))})
    first = pc.utf8_slice_codeunits(keys["sha"], 0, 1)
    for b in BUCKETS:
        part = keys.filter(pc.equal(first, b))
        if part.num_rows:
            pq.write_table(part, work / "a" / b / f"{file_id:06d}.parquet")
    return n


def phase_b(args):
    """Dropped (file, row) of one bucket plus the file holding the copy that was kept."""
    bucket, work = args
    paths = sorted((work / "a" / bucket).glob("*.parquet"))
    if not paths:
        return pa.table({k: pa.array([], t) for k, t in _EMPTY.items()})
    t = pa.concat_tables(pq.read_table(p) for p in paths).sort_by(
        [("sha", "ascending"), ("rank", "ascending"), ("doc_id", "ascending")])
    sha = t["sha"].combine_chunks()
    n = len(sha)
    is_start = np.ones(n, dtype=bool)
    if n > 1:
        is_start[1:] = ~pc.equal(sha.slice(1), sha.slice(0, n - 1)).to_numpy(zero_copy_only=False)
    run_start = np.maximum.accumulate(np.where(is_start, np.arange(n), 0))
    files, rows, dup = t["file"].to_numpy(), t["row"].to_numpy(), ~is_start
    return pa.table({"file": pa.array(files[dup], pa.int32()), "row": pa.array(rows[dup], pa.int32()),
                     "kept_file": pa.array(files[run_start[dup]], pa.int32())})


def phase_c(args):
    (_, _, path), drop_rows, out_dir = args
    table = pq.read_table(path)
    if drop_rows:
        keep = np.ones(table.num_rows, dtype=bool)
        keep[np.asarray(drop_rows, dtype=np.int64)] = False
        table = table.filter(pa.array(keep))
    pq.write_table(table, out_dir / path.name, compression="zstd", row_group_size=2000)
    return table.num_rows


def run(sources, workers, root=PROCESSED_DIR):
    files, work = _files(sources, root), root / WORK_DIR_NAME
    shutil.rmtree(work, ignore_errors=True)
    for b in BUCKETS:
        (work / "a" / b).mkdir(parents=True)
    staging = {}
    for source, origin, _ in files:
        if (source, origin) not in staging:
            staging[(source, origin)] = staging_dir(source, origin, root / STAGE)
    with ProcessPoolExecutor(max_workers=workers) as pool:
        docs_in = list(pool.map(phase_a, [(i, f, work) for i, f in enumerate(files)]))
        dropped = pa.concat_tables(pool.map(phase_b, [(b, work) for b in BUCKETS]))
        by_file = defaultdict(list)
        for f, r in zip(dropped["file"].to_pylist(), dropped["row"].to_pylist()):
            by_file[f].append(r)
        docs_out = list(pool.map(phase_c, [(f, by_file.get(i, []), staging[f[:2]]) for i, f in enumerate(files)]))
    for (source, origin), tmp in staging.items():
        publish(tmp, source, origin, root / STAGE)
    shutil.rmtree(work, ignore_errors=True)
    per_source = defaultdict(lambda: {"docs_in": 0, "docs_out": 0})
    for (source, _, _), n_in, n_out in zip(files, docs_in, docs_out):
        per_source[source]["docs_in"] += n_in
        per_source[source]["docs_out"] += n_out
    matrix = Counter((files[d][0], files[k][0]) for d, k in zip(dropped["file"].to_pylist(),
                                                               dropped["kept_file"].to_pylist()))
    stats = {"stage": STAGE, "sources": dict(sorted(per_source.items())),
             "dropped_to_kept": {f"{d} -> {k}": n for (d, k), n in sorted(matrix.items())}}
    (root / STAGE / "stats.json").write_text(json.dumps(stats, indent=1), encoding="utf-8")
    return stats


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("sources", nargs="*")
    parser.add_argument("--workers", type=int, default=8)
    args = parser.parse_args()
    sources = args.sources or sorted(d.name for d in (PROCESSED_DIR / IN_STAGE).iterdir() if d.is_dir())
    print(json.dumps(run(sources, args.workers), indent=1, ensure_ascii=False))


if __name__ == "__main__":
    main()
