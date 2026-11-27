"""API routes: /api/<path> -> module call. Runs under db.LOCK on the handler's shared connection."""
from studio import actions, corpus, items, jobs, sheets, stats, teacher
from taskset import content as tc
from taskset import import_eval, store


def _draft_ids(con, body):
    """Explicit item ids, or every draftable draft/pending item matching a review filter."""
    if body.get("item_ids"):
        return list(body["item_ids"])
    w, args = items.where(body.get("filter") or {})
    kinds = ",".join(f"'{k}'" for k in teacher.DRAFTABLE)
    extra = f"i.kind IN ({kinds}) AND i.status IN ('draft', 'pending')"
    return [r[0] for r in con.execute(f"SELECT i.id {items.BASE}{w}{' AND ' if w else ' WHERE '}{extra} "
                                      "ORDER BY i.id", args)]


def _item_api(h, method, item_id, action, body):
    con, by = h.con, h.user()
    if action is None and method == "GET":
        return items.detail(con, item_id)
    if action == "content" and method == "PUT":
        actions.edit(con, item_id, body["content"], by)
    elif action == "review":
        actions.review(con, item_id, body["verdict"], by, body.get("comment"))
    elif action == "correct":
        actions.correct(con, item_id, body["content"], by, body.get("comment"))
    elif action == "pending":
        actions.to_pending(con, item_id, by)
    elif action == "verify":
        return {"runs": actions.verify(con, item_id), "item": items.get_item(con, item_id)}
    elif action == "comment":
        actions.comment(con, item_id, body.get("body"), by)
        return items.detail(con, item_id)
    elif action == "flags":
        store.set_flags(con, item_id, body.get("flags") or [], by)
    else:
        raise FileNotFoundError("không có API này")
    return items.get_item(con, item_id)


def api(h, method, p, q, body):
    con = h.con
    if p == ["options"]:
        return {**items.options(con), "user": h.user(), "teacher": teacher.config(),
                "eval_datasets": sorted(import_eval.READERS), "draftable": list(teacher.DRAFTABLE)}
    if p == ["sources"]:
        return actions.save_source(con, body) if method == "POST" else actions.list_sources(con)
    if p == ["items"]:
        if method == "POST":
            item_id = actions.create(con, body["kind"], body["source"], body["content"], h.user(),
                                     submit=body.get("submit", True))
            return items.get_item(con, item_id)
        return items.list_items(con, q)
    if p == ["validate"] and method == "POST":
        return {"errors": tc.validate(body.get("kind"), body.get("content") or {}),
                "split": tc.split_of(body["kind"], body["content"]) if not tc.validate(
                    body.get("kind"), body.get("content") or {}) else None}
    if len(p) in (2, 3) and p[0] == "items":
        return _item_api(h, method, p[1], p[2] if len(p) == 3 else None, body)
    if p == ["draft"] and method == "POST":
        ids = _draft_ids(con, body)
        if not ids:
            raise ValueError("không có mục nào để teacher nháp (chỉ mục nháp/chờ duyệt thuộc loại hỗ trợ)")
        return {**jobs.submit(con, "draft", {"item_ids": ids}, h.user()), "items": len(ids)}
    if p == ["jobs"]:
        if method == "POST":
            return jobs.submit(con, body["kind"], body.get("params") or {}, h.user())
        return jobs.list_jobs(con)
    if len(p) == 3 and p[0] == "jobs" and p[2] == "cancel" and method == "POST":
        return jobs.cancel(con, int(p[1]))
    if p and p[0] == "corpus":
        return _corpus_api(p[1:], q, body)
    if p == ["sheets"]:
        return sheets.known()
    if p == ["sheets", "load"]:
        return sheets.load(q["path"])
    if p == ["sheets", "save"] and method == "POST":
        return sheets.save(body["path"], body["row"], body.get("value"), body.get("note"))
    if p == ["stats"]:
        return stats.stats(con)
    if p == ["targets"] and method == "POST":
        return stats.set_targets(con, body.get("targets"))
    if p == ["export"]:
        if method == "POST":
            return stats.run_export(con, (body.get("dir") or "").strip().strip('"') or None)
        return {**stats.exports(), "license": stats.license_gate(con)}
    raise FileNotFoundError("không có API này")


def _corpus_api(p, q, body):
    if p == []:
        return corpus.overview()
    if p == ["sample"]:
        return corpus.sample(q["stage"], q["source"], q.get("tier") or None, q.get("era") or None,
                             min(int(q.get("n") or 20), 100), q.get("seed"))
    if p == ["doc"]:
        return corpus.document(body["ref"])
    if p == ["search"]:
        return corpus.search(q["stage"], q["source"], q.get("q") or "", q.get("tier") or None)
    raise FileNotFoundError("không có API này")
