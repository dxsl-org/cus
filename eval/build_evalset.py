"""
Freeze an eval set declared in manifests/evalsets/<name>.yaml from reviewed TaskItems.

    uv run python -m eval.build_evalset evalset-v1 [--dry-run] [--db PATH]

Output DATA_ROOT/eval/<name>/: items.jsonl (one line per item, with its group),
decontam.jsonl (phase 6 13-gram list) and MANIFEST.json (per-item content sha256,
per-file sha256, counts). The directory is written once: if it exists the build stops,
because a frozen eval set that silently changes makes every score incomparable.
--dry-run counts what each group would take, including items still waiting for review.
"""
import argparse
import hashlib
import json
import sys
from collections import Counter
from datetime import datetime, timezone

import yaml

from pipeline.paths import DATA_ROOT, ROOT
from taskset import store
from taskset.export import decontam_entry

EVALSET_DIR = ROOT / "manifests" / "evalsets"
ACCEPTED = ("approved", "corrected")
_FIELDS = ("choices", "answer_key", "reference_answer", "rubric", "verifier")


def load(name):
    return yaml.safe_load((EVALSET_DIR / f"{name}.yaml").read_text(encoding="utf-8"))


def _candidates(con, group, statuses):
    marks = lambda xs: ",".join("?" * len(xs))  # noqa: E731
    rows = con.execute(
        "SELECT i.id, i.kind, i.split, i.source_id, v.version_no, v.content_json, v.content_sha256 FROM items i "
        "JOIN item_versions v ON v.id = i.current_version_id "
        f"WHERE i.split IN ('test', 'holdout') AND i.flags = '[]' AND i.status IN ({marks(statuses)}) "
        f"AND i.source_id IN ({marks(group['sources'])}) ORDER BY i.id",
        (*statuses, *group["sources"])).fetchall()
    domains, langs, meta = set(group.get("domains") or []), set(group.get("langs") or []), group.get("meta") or {}

    def keep(content):
        return ((not domains or content["domain"] in domains) and (not langs or content["lang"] in langs)
                and all((content.get("meta") or {}).get(k) == v for k, v in meta.items()))
    return [r for r in rows if keep(json.loads(r["content_json"]))]


def select(con, decl, statuses=ACCEPTED):
    """{group name: rows}. Sampling ranks item ids by salted hash, so it is stable as items are added."""
    taken, out = set(), {}
    for group in decl["groups"]:
        rows = [r for r in _candidates(con, group, statuses) if r["id"] not in taken]
        if group.get("sample"):
            key = lambda r: hashlib.sha256(f"{decl['sample_salt']}:{r['id']}".encode()).hexdigest()  # noqa: E731
            rows = sorted(sorted(rows, key=key)[: group["sample"]], key=lambda r: r["id"])
        taken.update(r["id"] for r in rows)
        out[group["name"]] = rows
    return out


def _line(group, row):
    data = json.loads(row["content_json"])
    obj = {"id": row["id"], "version": row["version_no"], "group": group, "kind": row["kind"], "split": row["split"],
           "lang": data["lang"], "domain": data["domain"], "messages": data["messages"]}
    if data.get("difficulty") is not None:  # added after evalset-v1, which has no difficulty field
        obj["difficulty"] = data["difficulty"]
    obj.update({k: data[k] for k in _FIELDS if data.get(k) is not None})
    obj["meta"] = data.get("meta", {})
    return obj


def build(con, name):
    decl = load(name)
    out_dir = DATA_ROOT / "eval" / decl["name"]
    if out_dir.exists():
        raise SystemExit(f"{out_dir} exists: frozen eval sets are never rebuilt; declare a new version")
    groups = select(con, decl)
    lines = [_line(g, r) for g, rows in groups.items() for r in rows]
    files = {"items.jsonl": "".join(json.dumps(o, ensure_ascii=False, sort_keys=True) + "\n" for o in lines),
             "decontam.jsonl": "".join(json.dumps(decontam_entry(o), ensure_ascii=False, sort_keys=True) + "\n"
                                       for o in lines)}
    manifest = {
        "name": decl["name"], "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "declaration_sha256": hashlib.sha256((EVALSET_DIR / f"{name}.yaml").read_bytes()).hexdigest(),
        "counts": {g: len(rows) for g, rows in groups.items()},
        "counts_by_lang": dict(Counter(f"{o['group']}:{o['lang']}" for o in lines)),
        "items": {r["id"]: r["content_sha256"] for rows in groups.values() for r in rows},
        "files": {n: hashlib.sha256(t.encode("utf-8")).hexdigest() for n, t in files.items()},
    }
    tmp = out_dir.with_name(f".{out_dir.name}.tmp")
    tmp.mkdir(parents=True)
    for n, text in files.items():
        (tmp / n).write_text(text, encoding="utf-8", newline="\n")
    (tmp / "MANIFEST.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.rename(out_dir)
    return out_dir, manifest["counts"]


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("name")
    parser.add_argument("--db", default=store.DEFAULT_DB)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--trial", metavar="PATH",
                        help="write would-be items (incl. unreviewed) to PATH for harness trial runs; not frozen")
    args = parser.parse_args()
    con = store.connect(args.db)
    if args.dry_run or args.trial:
        ready = select(con, load(args.name))
        waiting = select(con, load(args.name), ("pending", "auto", *ACCEPTED))
        for g in ready:
            print(f"{g:14} ready {len(ready[g]):5}   incl. unreviewed {len(waiting[g]):5}")
        if args.trial:
            with open(args.trial, "w", encoding="utf-8", newline="\n") as fh:
                fh.writelines(json.dumps(_line(g, r), ensure_ascii=False) + "\n"
                              for g, rows in waiting.items() for r in rows)
        return
    out_dir, counts = build(con, args.name)
    print(out_dir, counts)


if __name__ == "__main__":
    main()
