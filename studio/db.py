"""
Studio connection to the phase 8 task store (taskset/schema.sql stays the authority on item states),
plus the Studio's own tables: review comments and the background job queue.

One connection is shared by all request threads; every use goes through `LOCK`.
"""
import getpass
import hashlib
import json
import os
import threading
import unicodedata
from datetime import datetime, timezone

from taskset import store

LOCK = threading.RLock()
SCHEMA = """
CREATE TABLE IF NOT EXISTS studio_comments (
    id INTEGER PRIMARY KEY, item_id TEXT NOT NULL REFERENCES items (id), version_id INTEGER,
    author TEXT NOT NULL, body TEXT NOT NULL, at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS studio_comments_item ON studio_comments (item_id);
CREATE TRIGGER IF NOT EXISTS studio_comments_no_update BEFORE UPDATE ON studio_comments
BEGIN SELECT RAISE(ABORT, 'comments are append-only'); END;
CREATE TABLE IF NOT EXISTS studio_jobs (
    id INTEGER PRIMARY KEY, kind TEXT NOT NULL, params_json TEXT NOT NULL, state TEXT NOT NULL,
    total INTEGER, done INTEGER NOT NULL DEFAULT 0, message TEXT NOT NULL DEFAULT '',
    created_by TEXT NOT NULL, created_at TEXT NOT NULL, finished_at TEXT);
"""
# Added to phase 8 tables on open (ALTER when missing): the domain a source's new items start with.
COLUMNS = (("sources", "default_domain", "TEXT"),)


def now():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def fold(s):
    """Lower-case, strip Vietnamese diacritics (đ -> d) for accent-insensitive search."""
    s = (s or "").lower().replace("đ", "d")
    return "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")


def user():
    """Reviewer identity: LLM_STUDIO_USER, else the OS account."""
    return os.environ.get("LLM_STUDIO_USER") or getpass.getuser()


def connect(path=store.DEFAULT_DB):
    con = store.connect(path, shared=True)
    con.create_function("fold", 1, fold, deterministic=True)
    con.executescript(SCHEMA)
    con.create_function("rank", 2, lambda seed, key: hashlib.sha256(f"{seed}:{key}".encode()).hexdigest(),
                        deterministic=True)
    for table, column, decl in COLUMNS:
        if column not in {r[1] for r in con.execute(f"PRAGMA table_info({table})")}:
            con.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")
    return con


def get_meta(con, key, default=None):
    row = con.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return json.loads(row["value"]) if row else default


def set_meta(con, key, value):
    con.execute("INSERT INTO meta (key, value) VALUES (?, ?) ON CONFLICT (key) DO UPDATE SET value = excluded.value",
                (key, json.dumps(value, ensure_ascii=False)))
