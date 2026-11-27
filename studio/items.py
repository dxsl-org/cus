"""Item listing (review filters -> SQL), item detail with versions / reviews / verifier runs / comments."""
import json

from studio import db
from taskset import content as tc

BASE = ("FROM items i JOIN item_versions v ON v.id = i.current_version_id "
        "JOIN sources s ON s.id = i.source_id")
SORTS = {"id": "i.id", "newest": "i.updated_at DESC, i.id", "oldest": "i.updated_at, i.id", "random": "random()"}
ORIGINS = ("human", "model", "import")
# Verifier evidence on the current version, the phase 8 gate for accepting rl_verifiable (and for auto).
REF_PASS = ("EXISTS (SELECT 1 FROM verifier_runs r WHERE r.version_id = i.current_version_id "
            "AND r.role = 'reference' AND r.passed = 1)")
NEG_FAIL = ("EXISTS (SELECT 1 FROM verifier_runs r WHERE r.version_id = i.current_version_id "
            "AND r.role = 'negative' AND r.passed = 0)")
SELECT = (f"SELECT i.*, v.version_no, v.content_json, v.origin, v.created_by AS version_by, s.usage_scope, "
          f"{REF_PASS} AS ref_pass, {NEG_FAIL} AS neg_fail {BASE}")


def where(params):
    sql, args = [], []
    for name, expr in (("source", "i.source_id"), ("kind", "i.kind"), ("status", "i.status"), ("split", "i.split")):
        if params.get(name):
            sql.append(f"{expr} = ?")
            args.append(params[name])
    if params.get("domain"):
        sql.append("json_extract(v.content_json, '$.domain') = ?")
        args.append(params["domain"])
    if params.get("difficulty"):
        sql.append("json_extract(v.content_json, '$.difficulty') = ?")
        args.append(int(params["difficulty"]))
    if params.get("origin"):
        sql.append("v.origin LIKE ?")
        args.append(params["origin"] + ":%")
    flag = params.get("flag")
    if flag == "none":
        sql.append("i.flags = '[]'")
    elif flag:
        sql.append("i.flags LIKE ?")
        args.append(f'%"{flag}"%')
    if params.get("q"):
        sql.append("(fold(v.content_json) LIKE ? OR i.id LIKE ?)")
        args += [f"%{db.fold(params['q'])}%", f"%{params['q']}%"]
    return (" WHERE " + " AND ".join(sql)) if sql else "", args


def _public(row):
    d = dict(row)
    d["content"] = json.loads(d.pop("content_json"))
    d["flags"] = json.loads(d["flags"])
    d["ref_pass"], d["neg_fail"] = bool(d["ref_pass"]), bool(d["neg_fail"])
    return d


def list_items(con, params):
    w, args = where(params)
    total = con.execute(f"SELECT count(*) {BASE}{w}", args).fetchone()[0]
    size = min(max(int(params.get("size") or 30), 1), 200)
    page = max(int(params.get("page") or 1), 1)
    order = SORTS.get(params.get("sort") or "id", SORTS["id"])
    if order == "random()":  # seeded so paging through one random sample neither repeats nor skips items
        order = "rank(?, i.id)"
        args = args + [str(params.get("seed") or "")]
    rows = con.execute(f"{SELECT}{w} ORDER BY {order} LIMIT ? OFFSET ?", args + [size, (page - 1) * size]).fetchall()
    return {"total": total, "page": page, "size": size, "items": [_public(r) for r in rows]}


def get_item(con, item_id):
    row = con.execute(f"{SELECT} WHERE i.id = ?", (item_id,)).fetchone()
    if row is None:
        raise FileNotFoundError(f"không có mục {item_id}")
    return _public(row)


def detail(con, item_id):
    """Everything the review card's side panel shows; versions oldest first."""
    item = get_item(con, item_id)
    q = lambda sql: [dict(r) for r in con.execute(sql, (item_id,))]  # noqa: E731
    versions = q("SELECT id, version_no, parent_id, content_json, content_sha256, origin, created_by, created_at "
                 "FROM item_versions WHERE item_id = ? ORDER BY version_no")
    for v in versions:
        v["content"] = json.loads(v.pop("content_json"))
    return {"item": item, "versions": versions,
            "reviews": q("SELECT * FROM reviews WHERE item_id = ? ORDER BY id"),
            "runs": q("SELECT * FROM verifier_runs WHERE item_id = ? ORDER BY id"),
            "comments": q("SELECT * FROM studio_comments WHERE item_id = ? ORDER BY id"),
            "edits": q("SELECT * FROM edits WHERE item_id = ? ORDER BY id")}


def options(con):
    tax = tc.taxonomy()
    return {"kind": list(tc.KINDS), "status": ["draft", "pending", "auto", "approved", "corrected", "dropped"],
            "split": ["train", "dev", "test", "holdout"], "flag": [*sorted(tc.FLAGS), "none"],
            "origin": list(ORIGINS), "difficulty": tax["difficulty"], "domain": list(tax["domains"]),
            "domain_names": tax["domains"], "skill_names": tax["skills"], "taxonomy_version": tax["version"],
            "source": [r[0] for r in con.execute("SELECT id FROM sources ORDER BY id")],
            "usage_scope": ["train_ok", "research_only", "blocked"], "user": db.user()}
