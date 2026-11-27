"""
Export accepted TaskItems as an immutable training/eval snapshot.

    uv run python -m taskset.export [--db PATH] [--out DIR]

Writes <out>/<yyyymmdd>-<hash12>/{train,dev,test,holdout}.jsonl + decontam.jsonl + MANIFEST.json.
`hash` covers only the data files, so exporting an unchanged store twice gives the same
hash (and the existing directory is reused). The directory appears atomically (rename).

Acceptance: train/dev take auto + approved + corrected; test/holdout take
human verdicts only. Flagged items never export; train/dev also require a train_ok source.
decontam.jsonl lists every test/holdout prompt and answer for the phase 6 13-gram filter.
"""
import argparse
import hashlib
import json
import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

from pipeline.paths import DATA_ROOT
from taskset import store
from taskset.content import SPLIT_SALT

SPLITS = ("train", "dev", "test", "holdout")
ACCEPTED_IN = {"train": ("auto", "approved", "corrected"), "dev": ("auto", "approved", "corrected"),
               "test": ("approved", "corrected"), "holdout": ("approved", "corrected")}
DEFAULT_OUT = DATA_ROOT / "taskset" / "exports"
_OPTIONAL = ("chosen", "rejected", "reference_answer", "rubric", "choices", "answer_key", "verifier", "grounding")


def _rows(con, split):
    statuses = ACCEPTED_IN[split]
    return con.execute(
        "SELECT i.id, i.kind, i.split, i.status, s.id AS source, s.license, s.usage_scope, v.version_no, "
        "v.content_json, v.taxonomy_version, v.origin FROM items i "
        "JOIN item_versions v ON v.id = i.current_version_id JOIN sources s ON s.id = i.source_id "
        f"WHERE i.split = ? AND i.flags = '[]' AND i.status IN ({','.join('?' * len(statuses))}) "
        "ORDER BY i.id", (split, *statuses)).fetchall()


def _line(row):
    data = json.loads(row["content_json"])
    obj = {"id": row["id"], "version": row["version_no"], "kind": row["kind"], "split": row["split"],
           "messages": data["messages"]}
    obj.update({k: data[k] for k in _OPTIONAL if data.get(k) is not None})
    obj.update({"domain": data["domain"], "skills": data.get("skills", []), "difficulty": data.get("difficulty"),
                "lang": data["lang"], "origin": row["origin"], "label": row["status"], "source": row["source"],
                "license": row["license"], "taxonomy_version": row["taxonomy_version"]})
    return obj


def decontam_entry(obj):
    """Every prompt and answer text of an exported item, for the phase 6 13-gram filter."""
    answers = [obj.get("reference_answer"), obj.get("chosen")]
    if obj.get("choices"):
        answers += obj["choices"]
    texts = [m["content"] for m in obj["messages"]] + [a for a in answers if isinstance(a, str)]
    texts += [x for a in answers if isinstance(a, list) for x in a if isinstance(x, str)]
    return {"id": obj["id"], "split": obj["split"], "texts": texts}


def _dump(obj):
    return json.dumps(obj, ensure_ascii=False, sort_keys=True) + "\n"


def build(con):
    """In-memory file contents and counts; pure function of the store's accepted state."""
    files, counts, skipped = {}, {}, 0
    decontam = []
    for split in SPLITS:
        lines = []
        for row in _rows(con, split):
            if split in ("train", "dev") and row["usage_scope"] != "train_ok":
                skipped += 1
                continue
            obj = _line(row)
            lines.append(_dump(obj))
            if split in ("test", "holdout"):
                decontam.append(_dump(decontam_entry(obj)))
        files[f"{split}.jsonl"], counts[split] = "".join(lines), len(lines)
    files["decontam.jsonl"] = "".join(decontam)
    return files, counts, skipped


def export(con, out_root=DEFAULT_OUT):
    files, counts, skipped = build(con)
    digest = hashlib.sha256()
    for name in sorted(files):
        digest.update(f"{name}\0{files[name]}\0".encode("utf-8"))
    content_hash = digest.hexdigest()
    now = datetime.now(timezone.utc)
    target = out_root / f"{now:%Y%m%d}-{content_hash[:12]}"
    if target.exists():
        return target
    out_root.mkdir(parents=True, exist_ok=True)
    tmp = out_root / f".tmp-{os.getpid()}-{content_hash[:12]}"
    try:
        tmp.mkdir()
        for name, text in files.items():
            (tmp / name).write_text(text, encoding="utf-8", newline="\n")
        manifest = {"content_sha256": content_hash, "exported_at": now.isoformat(timespec="seconds"),
                    "split_salt": SPLIT_SALT, "accepted_in": ACCEPTED_IN, "counts": counts,
                    "skipped_not_train_ok": skipped,
                    "files": {n: hashlib.sha256(t.encode("utf-8")).hexdigest() for n, t in sorted(files.items())}}
        (tmp / "MANIFEST.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")
        tmp.rename(target)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return target


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default=store.DEFAULT_DB)
    parser.add_argument("--out", default=DEFAULT_OUT)
    args = parser.parse_args()
    from pathlib import Path
    print(export(store.connect(args.db), Path(args.out)))


if __name__ == "__main__":
    main()
