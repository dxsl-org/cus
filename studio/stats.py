"""Stats tab (counts, volume targets, reviewer agreement) and Export tab (taskset.export + license gate view)."""
import json
from collections import defaultdict
from itertools import combinations
from pathlib import Path

from studio import db
from taskset import export as tx

ACCEPTED = ("auto", "approved", "corrected")
TARGETS_KEY = "studio.targets"


def stats(con):
    grid = [dict(r) for r in con.execute(
        "SELECT i.kind, json_extract(v.content_json, '$.domain') AS domain, i.status, count(*) AS n FROM items i "
        "JOIN item_versions v ON v.id = i.current_version_id GROUP BY 1, 2, 3 ORDER BY 1, 2, 3")]
    accepted = defaultdict(int)
    for r in grid:
        if r["status"] in ACCEPTED:
            accepted[r["kind"]] += r["n"]
    targets = db.get_meta(con, TARGETS_KEY, {})
    return {"grid": grid, "by_source": [dict(r) for r in con.execute(
                "SELECT source_id, split, status, count(*) AS n FROM items GROUP BY 1, 2, 3 ORDER BY 1, 2, 3")],
            "targets": [{"kind": k, "target": t, "accepted": accepted.get(k, 0)} for k, t in sorted(targets.items())],
            "agreement": agreement(con)}


def set_targets(con, targets):
    clean = {k: int(v) for k, v in (targets or {}).items() if str(v).strip() not in ("", "0")}
    db.set_meta(con, TARGETS_KEY, clean)
    return clean


def agreement(con):
    """Pairwise agreement on versions judged by >= 2 reviewers (each reviewer's latest verdict on that version)."""
    latest = {}
    for r in con.execute("SELECT version_id, reviewer, verdict FROM reviews ORDER BY id"):
        latest[(r["version_id"], r["reviewer"])] = "approved" if r["verdict"] == "corrected" else r["verdict"]
    by_version = defaultdict(list)
    for (version, _), verdict in latest.items():
        by_version[version].append(verdict)
    pairs = [a == b for verdicts in by_version.values() if len(verdicts) > 1 for a, b in combinations(verdicts, 2)]
    return {"reviewers": len({r for _, r in latest}), "versions": sum(len(v) > 1 for v in by_version.values()),
            "pairs": len(pairs), "agree": sum(pairs) / len(pairs) if pairs else None}


def license_gate(con):
    """What each source may feed: train/dev need train_ok; flagged items never export."""
    return [dict(r) for r in con.execute(
        "SELECT s.id, s.license, s.usage_scope, i.split, count(*) AS accepted, "
        "sum(i.flags <> '[]') AS flagged FROM items i JOIN sources s ON s.id = i.source_id "
        f"WHERE i.status IN ({','.join('?' * len(ACCEPTED))}) GROUP BY 1, 4 ORDER BY 1, 4", ACCEPTED)]


def exports():
    root = Path(tx.DEFAULT_OUT)
    out = []
    for d in sorted(root.glob("*-*"), reverse=True)[:20] if root.is_dir() else []:
        manifest = d / "MANIFEST.json"
        if manifest.is_file():
            m = json.loads(manifest.read_text(encoding="utf-8"))
            out.append({"dir": str(d), "counts": m["counts"], "exported_at": m["exported_at"],
                        "skipped_not_train_ok": m.get("skipped_not_train_ok", 0)})
    return {"default_dir": str(root), "exports": out}


def run_export(con, out_dir=None):
    target = tx.export(con, Path(out_dir) if out_dir else Path(tx.DEFAULT_OUT))
    return {"dir": str(target), "manifest": json.loads((target / "MANIFEST.json").read_text(encoding="utf-8"))}
