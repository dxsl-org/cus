"""
Every Studio mutation of an item, mapped onto taskset.store (which logs `edits` and whose DB triggers
reject illegal states). Wrong-answer samples for a verifier live in content.meta.negatives.
"""
from studio import db
from taskset import store

VERDICTS = ("approved", "dropped", "needs_work")


def reference_candidate(content):
    """The answer the item's own verifier must accept: the key, or the first of several accepted answers."""
    ref = content["answer_key"] if "answer_key" in content else content.get("reference_answer")
    return ref[0] if isinstance(ref, list) else ref


def negatives(content):
    return [str(x) for x in (content.get("meta") or {}).get("negatives") or [] if str(x).strip()]


def _status(con, item_id):
    return store._item(con, item_id)["status"]


def review(con, item_id, verdict, by, comment=None):
    """Enter / Ctrl+D / needs-work from the review tab. A draft is submitted first: review needs pending."""
    if verdict not in VERDICTS:
        raise ValueError(f"không có kết luận {verdict}")
    if verdict == "approved" and _status(con, item_id) == "draft":
        store.submit(con, item_id, by)
    store.review(con, item_id, by, verdict, comment or None)


def correct(con, item_id, content, by, comment=None):
    """The reviewer's fix becomes a new version, reviewed `corrected` in the same step."""
    if _status(con, item_id) == "draft":
        store.submit(con, item_id, by)
    store.review(con, item_id, by, "corrected", comment or None, corrected_content=content)


def to_pending(con, item_id, by):
    """Ctrl+R: back to the review queue, also from dropped (via draft, the schema's only exit)."""
    if _status(con, item_id) == "dropped":
        store.restore(con, item_id, by)
    store.submit(con, item_id, by)


def verify(con, item_id):
    """Ctrl+T: the reference must pass and every wrong sample must fail; each run is recorded."""
    content = store.current_content(con, item_id)
    if not content.get("verifier"):
        raise ValueError("mục này không có verifier")
    ref = reference_candidate(content)
    if ref is None:
        raise ValueError("chưa có đáp án chuẩn để chạy verifier")
    runs = [{"role": "reference", "candidate": str(ref), "expect": True}]
    runs += [{"role": "negative", "candidate": n, "expect": False} for n in negatives(content)]
    for run in runs:
        run["passed"], run["detail"] = store.run_verifier(con, item_id, run["candidate"], run["role"])
        run["ok"] = run["passed"] == run["expect"]
    return runs


def comment(con, item_id, body, by):
    body = str(body or "").strip()
    if not body:
        raise ValueError("bình luận trống")
    item = store._item(con, item_id)
    with store._tx(con):
        con.execute("INSERT INTO studio_comments (item_id, version_id, author, body, at) VALUES (?, ?, ?, ?, ?)",
                    (item_id, item["current_version_id"], by, body, db.now()))


def create(con, kind, source_id, content, by, origin=None, submit=True):
    """Compose tab: a new item (split fixed by the store), sent to review unless kept as draft."""
    item_id = store.create_item(con, kind, source_id, content, origin or f"human:{by}", by)
    if submit:
        store.submit(con, item_id, by)
    return item_id


def edit(con, item_id, content, by, origin=None):
    """New version; an accepted item drops back to pending (store.edit_content)."""
    return store.edit_content(con, item_id, content, origin or f"human:{by}", by)


def save_source(con, body):
    source_id = str(body.get("id") or "").strip()
    if not source_id or not str(body.get("license") or "").strip():
        raise ValueError("cần mã nguồn và giấy phép")
    store.add_source(con, source_id, body["license"].strip(), body.get("usage_scope") or "", body.get("note") or None)
    con.execute("UPDATE sources SET default_domain = ? WHERE id = ?", (body.get("default_domain") or None, source_id))
    return list_sources(con)


def list_sources(con):
    return [dict(r) for r in con.execute(
        "SELECT s.*, (SELECT count(*) FROM items i WHERE i.source_id = s.id) AS items, "
        "(SELECT count(*) FROM items i WHERE i.source_id = s.id AND i.status = 'pending') AS pending "
        "FROM sources s ORDER BY s.id")]
