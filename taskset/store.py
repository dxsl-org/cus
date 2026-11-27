"""
SQLite store for TaskItems. Every mutation runs in one transaction and is logged in
`edits`; the DB triggers (schema.sql) are the final authority on legal states, so a
ValueError here is a friendly early check and sqlite3.IntegrityError the hard stop.
"""
import hashlib
import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

from pipeline.paths import DATA_ROOT
from taskset import content as tc
from taskset import verifiers

SCHEMA_PATH = Path(__file__).with_name("schema.sql")
DEFAULT_DB = DATA_ROOT / "taskset" / "taskset.db"


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def connect(path=DEFAULT_DB, shared=False):
    """`shared`: one connection used by several threads (the Studio server); the caller serializes access."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path, isolation_level=None, check_same_thread=not shared, timeout=30)  # BEGIN/COMMIT in _tx
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    con.execute("PRAGMA journal_mode = WAL")
    con.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    return con


class _tx:
    def __init__(self, con):
        self.con = con

    def __enter__(self):
        self.con.execute("BEGIN IMMEDIATE")
        return self.con

    def __exit__(self, exc_type, *_):
        self.con.execute("ROLLBACK" if exc_type else "COMMIT")


def _log(con, item_id, field, old, new, by):
    con.execute("INSERT INTO edits (item_id, field, old_value, new_value, by, at) VALUES (?, ?, ?, ?, ?, ?)",
                (item_id, field, None if old is None else str(old), None if new is None else str(new), by, _now()))


def _item(con, item_id):
    row = con.execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone()
    if row is None:
        raise KeyError(f"no item {item_id!r}")
    return row


def _set_status(con, item, status, by):
    if status != item["status"]:
        con.execute("UPDATE items SET status = ?, updated_at = ? WHERE id = ?", (status, _now(), item["id"]))
        _log(con, item["id"], "status", item["status"], status, by)


def current_content(con, item_id):
    row = con.execute("SELECT v.content_json FROM items i JOIN item_versions v ON v.id = i.current_version_id "
                      "WHERE i.id = ?", (item_id,)).fetchone()
    return json.loads(row["content_json"]) if row else None


def add_source(con, source_id, license, usage_scope, note=None):
    with _tx(con):
        con.execute("INSERT INTO sources (id, license, usage_scope, note) VALUES (?, ?, ?, ?) "
                    "ON CONFLICT (id) DO UPDATE SET license = excluded.license, "
                    "usage_scope = excluded.usage_scope, note = excluded.note",
                    (source_id, license, usage_scope, note))


def _insert_version(con, item_id, content, origin, by):
    errors = tc.validate(_item(con, item_id)["kind"], content)
    if errors:
        raise ValueError(f"{item_id}: " + "; ".join(errors))
    text, sha = tc.canonical(content)
    prev = con.execute("SELECT id, version_no FROM item_versions WHERE item_id = ? ORDER BY version_no DESC LIMIT 1",
                       (item_id,)).fetchone()
    cur = con.execute(
        "INSERT INTO item_versions (item_id, version_no, parent_id, content_json, content_sha256, taxonomy_version, "
        "origin, created_by, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (item_id, prev["version_no"] + 1 if prev else 1, prev["id"] if prev else None, text, sha,
         tc.taxonomy()["version"], origin, by, _now()))
    return cur.lastrowid


def _point_to(con, item_id, version_id, by):
    old = _item(con, item_id)["current_version_id"]
    con.execute("UPDATE items SET current_version_id = ?, updated_at = ? WHERE id = ?", (version_id, _now(), item_id))
    _log(con, item_id, "version", old, version_id, by)


def create_item(con, kind, source_id, content, origin, by, split=None, item_id=None):
    """New draft item with version 1. Eval kinds and non-train_ok sources may not land in train/dev."""
    split = split or tc.split_of(kind, content)
    scope = con.execute("SELECT usage_scope FROM sources WHERE id = ?", (source_id,)).fetchone()
    if scope is None:
        raise KeyError(f"unknown source {source_id!r}; add_source first")
    if split in ("train", "dev") and (kind in tc.EVAL_KINDS or scope["usage_scope"] != "train_ok"):
        raise ValueError(f"{kind} from {source_id} ({scope['usage_scope']}) cannot go to {split}")
    item_id = item_id or uuid.uuid4().hex
    with _tx(con):
        now = _now()
        con.execute("INSERT INTO items (id, kind, source_id, split, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                    (item_id, kind, source_id, split, now, now))
        _point_to(con, item_id, _insert_version(con, item_id, content, origin, by), by)
    return item_id


def edit_content(con, item_id, content, origin, by):
    """New version. An accepted item goes back to pending: its acceptance was for the old content."""
    with _tx(con):
        item = _item(con, item_id)
        if item["status"] == "dropped":
            raise ValueError(f"{item_id} is dropped; restore it to draft first")
        version_id = _insert_version(con, item_id, content, origin, by)
        if item["status"] in ("auto", "approved", "corrected"):
            _set_status(con, item, "pending", by)
        _point_to(con, item_id, version_id, by)
    return version_id


def submit(con, item_id, by):
    with _tx(con):
        _set_status(con, _item(con, item_id), "pending", by)


def restore(con, item_id, by):
    """A dropped item back to draft (the only way out of dropped)."""
    with _tx(con):
        _set_status(con, _item(con, item_id), "draft", by)


def review(con, item_id, reviewer, verdict, comment=None, corrected_content=None):
    """
    Record a human verdict on the current version and move status accordingly.
    `corrected` stores the reviewer's content as a new version and reviews that version.
    """
    if (verdict == "corrected") != (corrected_content is not None):
        raise ValueError("corrected_content is required for, and only for, verdict 'corrected'")
    with _tx(con):
        item = _item(con, item_id)
        version_id = item["current_version_id"]
        if verdict == "corrected":
            version_id = _insert_version(con, item_id, corrected_content, f"human:{reviewer}", reviewer)
        con.execute("INSERT INTO reviews (item_id, version_id, reviewer, verdict, comment, at) "
                    "VALUES (?, ?, ?, ?, ?, ?)", (item_id, version_id, reviewer, verdict, comment, _now()))
        if verdict == "corrected":
            # Pointer and status move together: the item is never "approved" on an unreviewed version.
            con.execute("UPDATE items SET current_version_id = ?, status = 'corrected', updated_at = ? WHERE id = ?",
                        (version_id, _now(), item_id))
            _log(con, item_id, "version", item["current_version_id"], version_id, reviewer)
            _log(con, item_id, "status", item["status"], "corrected", reviewer)
        else:
            _set_status(con, item, "draft" if verdict == "needs_work" else verdict, reviewer)


def run_verifier(con, item_id, candidate, role):
    """Check `candidate` (role reference | negative | candidate) on the current version; record and return the run."""
    with _tx(con):
        item = _item(con, item_id)
        data = current_content(con, item_id)
        if not data.get("verifier"):
            raise ValueError(f"{item_id} has no verifier")
        passed, detail = verifiers.check(data["verifier"], candidate, data)
        con.execute("INSERT INTO verifier_runs (item_id, version_id, role, candidate_sha256, passed, detail, at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (item_id, item["current_version_id"], role, hashlib.sha256(str(candidate).encode()).hexdigest(),
                     int(passed), detail, _now()))
    return passed, detail


def mark_auto(con, item_id, by="verifier"):
    with _tx(con):
        _set_status(con, _item(con, item_id), "auto", by)


def set_flags(con, item_id, flags, by):
    unknown = set(flags) - tc.FLAGS
    if unknown:
        raise ValueError(f"unknown flags {sorted(unknown)}")
    with _tx(con):
        item, new = _item(con, item_id), json.dumps(sorted(set(flags)))
        if new != item["flags"]:
            con.execute("UPDATE items SET flags = ?, updated_at = ? WHERE id = ?", (new, _now(), item_id))
            _log(con, item_id, "flags", item["flags"], new, by)
