"""
Append-only sidecar stores for fixes recovered after the original crawl.
Exports merge them in; the latest row per doc_id wins.

Layout: PATCHES_DIR/<kind>/<source_id>/batch-<utc-timestamp>-<n>.parquet

- PUBLISH_DATES: dates from re-fetched page <head>. A failed lookup is stored with
  publish_date = null so it is not retried forever.
"""
import itertools
from datetime import datetime, timezone

import pyarrow as pa
import pyarrow.parquet as pq

from pipeline.paths import PATCHES_DIR

_counter = itertools.count()


class PatchStore:
    def __init__(self, kind, schema):
        self.kind = kind
        self.schema = schema

    def directory(self, source_id):
        return PATCHES_DIR / self.kind / source_id

    def load(self, source_id):
        """Map doc_id -> row dict (without doc_id); later batches override earlier ones."""
        result = {}
        for path in sorted(self.directory(source_id).glob("batch-*.parquet")):
            for row in pq.read_table(path).to_pylist():
                result[row.pop("doc_id")] = row
        return result

    def append(self, source_id, rows):
        """Write one batch; missing nullable fields default to None, fetched_at to now."""
        if not rows:
            return None
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        rows = [{**{name: None for name in self.schema.names}, "fetched_at": now, **row} for row in rows]
        directory = self.directory(source_id)
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"batch-{now:%Y%m%dT%H%M%S}-{next(_counter):05d}.parquet"
        pq.write_table(pa.Table.from_pylist(rows, schema=self.schema), path, compression="zstd")
        return path


PUBLISH_DATES = PatchStore("publish_dates", pa.schema([
    ("doc_id", pa.string()),
    ("publish_date", pa.date32()),
    ("tag", pa.string()),
    ("detail", pa.string()),
    ("fetched_at", pa.timestamp("ms")),
]))
