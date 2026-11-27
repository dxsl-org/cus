"""
Background job queue (studio_jobs) run by one worker thread on its own connection: teacher drafts,
file imports and public eval imports. Jobs run one at a time in id order; a job left `running` by a
crash or power cut is re-queued at start, which is safe because every kind skips finished work.
"""
import json
import threading
import traceback

from studio import db, importer, teacher
from taskset import import_eval, store

KINDS = ("draft", "import_file", "import_eval")


def submit(con, kind, params, by):
    if kind not in KINDS:
        raise ValueError(f"không có loại việc {kind}")
    with store._tx(con):
        cur = con.execute("INSERT INTO studio_jobs (kind, params_json, state, created_by, created_at) "
                          "VALUES (?, ?, 'queued', ?, ?)", (kind, json.dumps(params, ensure_ascii=False), by, db.now()))
    return {"id": cur.lastrowid}


def list_jobs(con, limit=30):
    return [dict(r) for r in con.execute("SELECT * FROM studio_jobs ORDER BY id DESC LIMIT ?", (limit,))]


def cancel(con, job_id):
    con.execute("UPDATE studio_jobs SET state = 'cancelled', finished_at = ? WHERE id = ? AND state = 'queued'",
                (db.now(), job_id))
    return {"ok": True}


class Worker:
    def __init__(self, path):
        self.con = db.connect(path)
        self.con.execute("UPDATE studio_jobs SET state = 'queued' WHERE state = 'running'")
        self.wake = threading.Event()
        threading.Thread(target=self._loop, daemon=True, name="studio-jobs").start()

    def _update(self, job_id, **fields):
        sets = ", ".join(f"{k} = ?" for k in fields)
        self.con.execute(f"UPDATE studio_jobs SET {sets} WHERE id = ?", (*fields.values(), job_id))

    def _loop(self):
        while True:
            job = self.con.execute("SELECT * FROM studio_jobs WHERE state = 'queued' ORDER BY id LIMIT 1").fetchone()
            if job is None:
                self.wake.wait(2)
                self.wake.clear()
                continue
            self._update(job["id"], state="running")
            try:
                result = getattr(self, "_" + job["kind"])(job["id"], json.loads(job["params_json"]), job["created_by"])
                self._update(job["id"], state="done", finished_at=db.now(),
                             message=json.dumps(result, ensure_ascii=False))
            except Exception as e:  # a failing job must never stop the queue
                traceback.print_exc()
                self._update(job["id"], state="error", finished_at=db.now(), message=f"{type(e).__name__}: {e}")

    def _draft(self, job_id, params, by):
        """Teacher answer for each item still in draft/pending; already model-drafted current versions are skipped."""
        model = teacher.model_name()
        ids = params["item_ids"]
        self._update(job_id, total=len(ids))
        stats, errors = {"drafted": 0, "skipped": 0, "failed": 0}, []
        for n, item_id in enumerate(ids, start=1):
            item = self.con.execute("SELECT i.kind, i.status, v.origin FROM items i JOIN item_versions v "
                                    "ON v.id = i.current_version_id WHERE i.id = ?", (item_id,)).fetchone()
            if item is None or item["status"] not in ("draft", "pending") or item["origin"].startswith("model:"):
                stats["skipped"] += 1
            else:
                try:
                    data = teacher.draft(item["kind"], store.current_content(self.con, item_id), model)
                    store.edit_content(self.con, item_id, data, f"model:{model}", by)
                    store.submit(self.con, item_id, by)
                    stats["drafted"] += 1
                except (ValueError, RuntimeError) as e:
                    stats["failed"] += 1
                    errors.append(f"{item_id}: {e}")
            self._update(job_id, done=n)
        return {**stats, "model": model, "errors": errors[:50]}

    def _import_file(self, job_id, params, by):
        return importer.import_file(self.con, params["path"], params["source"],
                                    progress=lambda done: self._update(job_id, done=done))

    def _import_eval(self, job_id, params, by):
        names = params["datasets"]
        self._update(job_id, total=len(names))
        out = {}
        for n, name in enumerate(names, start=1):
            out[name] = import_eval.import_dataset(self.con, name)
            self._update(job_id, done=n)
        return out
