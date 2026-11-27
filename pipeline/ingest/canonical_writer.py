"""
Write DocumentRecords as Parquet shards under CANONICAL_DIR/<source_id>/<origin>/,
replacing the previous export of that (source, origin) atomically: shards go to a
hidden staging dir and are swapped in on publish. Readers never see a half-written
export; an aborted run leaves the old one intact.

`origin` separates independent producers of the same source (legacy SQLite export,
live crawl, dump, open dataset) so re-running one never deletes another's output.
Readers open CANONICAL_DIR/<source_id> recursively; pyarrow skips "."-prefixed dirs.
"""
import os
import shutil

import pyarrow as pa
import pyarrow.parquet as pq

from pipeline.paths import CANONICAL_DIR
from pipeline.schema.document_record import ARROW_SCHEMA

MAX_SHARD_TEXT_BYTES = 512 * 2**20  # uncompressed text; ~150-250 MB on disk with zstd
ROW_GROUP_ROWS = 2000
ORIGINS = frozenset({"legacy", "crawl", "dump", "hf", "book", "ebook"})


class ShardWriter:
    """
    Single writer: ShardWriter(source_id, origin) ... commit().
    Parallel writers: the coordinator creates `tmp_dir` via staging_dir(); each worker uses
    ShardWriter(source_id, origin, tmp_dir=..., shard_prefix=<unique>) and close(); the
    coordinator then calls publish(tmp_dir, source_id, origin).
    """

    def __init__(self, source_id, origin, root=CANONICAL_DIR, tmp_dir=None, shard_prefix="part"):
        if origin not in ORIGINS:
            raise ValueError(f"origin {origin!r} not in {sorted(ORIGINS)}")
        self.source_id, self.origin, self.root = source_id, origin, root
        self.owns_tmp = tmp_dir is None
        self.tmp_dir = staging_dir(source_id, origin, root) if self.owns_tmp else tmp_dir
        self.shard_prefix = shard_prefix
        self._writer, self._shard, self._shard_bytes, self._buffer = None, -1, 0, []
        self.shards = 0

    def write(self, record):
        self._buffer.append(record)
        self._shard_bytes += len(record["text"].encode("utf-8"))
        if len(self._buffer) >= ROW_GROUP_ROWS:
            self._flush()
        if self._shard_bytes >= MAX_SHARD_TEXT_BYTES:
            self._flush()
            self._writer.close()
            self._writer, self._shard_bytes = None, 0

    def _flush(self):
        if not self._buffer:
            return
        if self._writer is None:
            self._shard += 1
            path = self.tmp_dir / f"{self.shard_prefix}-{self._shard:05d}.parquet"
            self._writer = pq.ParquetWriter(path, ARROW_SCHEMA, compression="zstd")
            self.shards += 1
        self._writer.write_table(pa.Table.from_pylist(self._buffer, schema=ARROW_SCHEMA))
        self._buffer = []

    def close(self):
        self._flush()
        if self._writer is not None:
            self._writer.close()
            self._writer = None

    def commit(self):
        self.close()
        publish(self.tmp_dir, self.source_id, self.origin, self.root)

    def abort(self):
        if self._writer is not None:
            self._writer.close()
        if self.owns_tmp:
            shutil.rmtree(self.tmp_dir, ignore_errors=True)


def staging_dir(source_id, origin, root=CANONICAL_DIR):
    tmp = root / source_id / f".{origin}.tmp-{os.getpid()}"
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True)
    return tmp


def publish(tmp_dir, source_id, origin, root=CANONICAL_DIR):
    """Swap a fully written staging dir in place of the current (source, origin) export."""
    final_dir = root / source_id / origin
    backup = final_dir.with_name(f".{origin}.old-{os.getpid()}")
    if final_dir.exists():
        final_dir.rename(backup)
    tmp_dir.rename(final_dir)
    shutil.rmtree(backup, ignore_errors=True)
