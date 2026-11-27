"""
Per-source crawl state in SQLite (DATA_ROOT/crawl_state/<source_id>.sqlite).

Every URL ever discovered is a row; nothing is deleted, so a killed crawl resumes
exactly where it stopped: `pending` rows are the to-do list, `done` rows point at
their raw response in WARC (`raw_ref`), and `group_key`/`seq` let extraction
reassemble multi-page documents (forum threads) in order.
"""
import sqlite3
import time

from pipeline.paths import DATA_ROOT

SCHEMA = """
CREATE TABLE IF NOT EXISTS urls (
    url TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'done', 'failed', 'skipped')),
    priority INTEGER NOT NULL DEFAULT 0,
    depth INTEGER NOT NULL DEFAULT 0,
    attempts INTEGER NOT NULL DEFAULT 0,
    group_key TEXT,
    seq INTEGER NOT NULL DEFAULT 0,
    captured_at TEXT,          -- archive capture time (Wayback/CC); NULL = live fetch
    http_status INTEGER,
    raw_ref TEXT,
    fetched_at TEXT,
    error TEXT,
    discovered_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS urls_todo ON urls (status, priority DESC, attempts, discovered_at);
CREATE INDEX IF NOT EXISTS urls_group ON urls (group_key, seq);
"""


def state_path(source_id):
    return DATA_ROOT / "crawl_state" / f"{source_id}.sqlite"


class Frontier:
    def __init__(self, source_id, path=None):
        path = path or state_path(source_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, isolation_level=None)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript(SCHEMA)

    def add(self, links, depth=0):
        """Insert new links (crawler.adapters.Link); already-known URLs are left untouched."""
        now = time.strftime("%Y-%m-%dT%H:%M:%S")
        rows = [(l.url, l.kind, l.priority, depth, l.group_key, l.seq, l.captured_at, now) for l in links]
        # One transaction: in autocommit mode every row would be its own fsync (a 50k-URL
        # sitemap took minutes).
        self.db.execute("BEGIN")
        try:
            cur = self.db.executemany(
                "INSERT OR IGNORE INTO urls (url, kind, priority, depth, group_key, seq, captured_at, discovered_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)", rows)
            self.db.execute("COMMIT")
        except BaseException:
            self.db.execute("ROLLBACK")
            raise
        return cur.rowcount

    def claim(self, limit, max_attempts, kinds=None):
        """Next pending rows (optionally only `kinds`), highest priority first. Re-claimable until marked."""
        kind_filter = f"AND kind IN ({','.join('?' * len(kinds))}) " if kinds else ""
        return self.db.execute(
            "SELECT url, kind, depth, group_key, seq, captured_at FROM urls WHERE status = 'pending' "
            f"AND attempts < ? {kind_filter}ORDER BY priority DESC, attempts, discovered_at LIMIT ?",
            (max_attempts, *(kinds or ()), limit)).fetchall()

    def mark_done(self, url, http_status, raw_ref):
        self.db.execute("UPDATE urls SET status='done', http_status=?, raw_ref=?, fetched_at=?, error=NULL "
                        "WHERE url=?", (http_status, raw_ref, time.strftime("%Y-%m-%dT%H:%M:%S"), url))

    def mark_failed(self, url, error, http_status=None, final=False):
        """Count an attempt; `final` (e.g. 404, robots) stops retries immediately."""
        self.db.execute("UPDATE urls SET attempts = attempts + 1, error=?, http_status=?, fetched_at=?, "
                        "status = CASE WHEN ? THEN 'failed' ELSE status END WHERE url=?",
                        (error, http_status, time.strftime("%Y-%m-%dT%H:%M:%S"), final, url))

    def requeue(self, url, error):
        """A fetched page that could not be parsed (e.g. truncated body): count an attempt, fetch again."""
        self.db.execute("UPDATE urls SET status='pending', attempts = attempts + 1, error=? WHERE url=?", (error, url))

    def mark_skipped(self, url, reason):
        self.db.execute("UPDATE urls SET status='skipped', error=? WHERE url=?", (reason, url))

    def exhaust(self, max_attempts):
        """Pending rows that used up their attempts become failed."""
        self.db.execute("UPDATE urls SET status='failed' WHERE status='pending' AND attempts >= ?", (max_attempts,))

    def stats(self):
        return dict(self.db.execute("SELECT kind || ':' || status, COUNT(*) FROM urls GROUP BY 1").fetchall())

    def done_groups(self, kinds):
        """Yield (group_key, [(url, kind, seq, raw_ref, fetched_at, captured_at)]) for fetched rows, in order."""
        marks = ",".join("?" * len(kinds))
        rows = self.db.execute(
            f"SELECT COALESCE(group_key, url), url, kind, seq, raw_ref, fetched_at, captured_at FROM urls "
            f"WHERE status='done' AND kind IN ({marks}) ORDER BY COALESCE(group_key, url), seq", kinds)
        group, members = None, []
        for key, *member in rows:
            if key != group and members:
                yield group, members
                members = []
            group = key
            members.append(tuple(member))
        if members:
            yield group, members
