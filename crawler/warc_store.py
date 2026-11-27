"""
Raw response archive. Every fetched response is written to WARC *before* parsing,
so extraction can be re-run offline with better extractors at any time.

Files: RAW_DIR/<source_id>/<YYYY-MM>/<source_id>-<timestamp>-<pid>.warc.gz, rotated at
~1 GiB. Each record is its own gzip member, so `raw_ref` = "warc://<path relative to
DATA_ROOT>#<byte offset>" supports random access without scanning the file.
Archive captures (Wayback/CC) carry the original capture time in "X-Capture-Date".
"""
import io
import os
import time
from datetime import datetime, timezone

from warcio.archiveiterator import ArchiveIterator
from warcio.statusandheaders import StatusAndHeaders
from warcio.warcwriter import WARCWriter

from pipeline.paths import DATA_ROOT, RAW_DIR

MAX_FILE_BYTES = 2**30


class WarcStore:
    def __init__(self, source_id):
        self.source_id = source_id
        self._file, self._writer, self._path = None, None, None

    def _open(self):
        month = datetime.now(timezone.utc).strftime("%Y-%m")
        directory = RAW_DIR / self.source_id / month
        directory.mkdir(parents=True, exist_ok=True)
        self._path = directory / f"{self.source_id}-{time.strftime('%Y%m%dT%H%M%S')}-{os.getpid()}.warc.gz"
        self._file = open(self._path, "ab")
        self._writer = WARCWriter(self._file, gzip=True)

    def write(self, url, status, headers, body, captured_at=None):
        """Append one response record; returns its raw_ref."""
        if self._file is None or self._file.tell() >= MAX_FILE_BYTES:
            self.close()
            self._open()
        http_headers = StatusAndHeaders(f"{status} -", [(k, v) for k, v in headers.items()
                                                         if k.lower() not in ("content-encoding", "transfer-encoding")],
                                        protocol="HTTP/1.1")
        warc_headers = {"X-Capture-Date": captured_at} if captured_at else None
        record = self._writer.create_warc_record(url, "response", payload=io.BytesIO(body),
                                                 http_headers=http_headers, warc_headers_dict=warc_headers)
        offset = self._file.tell()
        self._writer.write_record(record)
        self._file.flush()
        return f"warc://{self._path.relative_to(DATA_ROOT).as_posix()}#{offset}"

    def close(self):
        if self._file is not None:
            self._file.close()
            self._file = None


def read(raw_ref):
    """Return (url, status, headers dict, body bytes) for a raw_ref."""
    path, offset = raw_ref.removeprefix("warc://").rsplit("#", 1)
    with open(DATA_ROOT / path, "rb") as f:
        f.seek(int(offset))
        record = next(iter(ArchiveIterator(f)))
        status = int(record.http_headers.get_statuscode())
        headers = dict(record.http_headers.headers)
        return record.rec_headers.get_header("WARC-Target-URI"), status, headers, record.content_stream().read()
