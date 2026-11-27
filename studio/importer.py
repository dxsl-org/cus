"""
File import into one registered source, as pending items (origin import:<file name>).

JSONL: one {"kind", "content", "id"?} per line (content = TaskItem content, taskset.content).
CSV (UTF-8, header row): kind, question, answer, and optional system, domain, lang, difficulty,
skills (comma-separated). Missing domain takes the source's default domain; lang defaults to vi.
Item id = "<source>:<id>", or "<source>:" + hash of the user turns, so re-importing adds only new items.
"""
import csv
import hashlib
import json
from pathlib import Path

from taskset import content as tc
from taskset import store

BY = "import"


def _csv_content(row, default_domain):
    question, answer = (row.get("question") or "").strip(), (row.get("answer") or "").strip()
    kind = (row.get("kind") or "sft").strip()
    messages = ([{"role": "system", "content": row["system"].strip()}] if (row.get("system") or "").strip() else [])
    messages.append({"role": "user", "content": question})
    data = {"domain": (row.get("domain") or "").strip() or default_domain, "lang": (row.get("lang") or "vi").strip(),
            "skills": [s.strip() for s in (row.get("skills") or "").split(",") if s.strip()], "messages": messages}
    if (row.get("difficulty") or "").strip():
        data["difficulty"] = int(row["difficulty"])
    if kind in ("sft", "reasoning"):
        messages.append({"role": "assistant", "content": answer})
    elif answer:
        data["reference_answer"] = answer
    return kind, data, None


def _lines(path, default_domain):
    """(line number, kind, content, external id) per record; malformed records raise with their line."""
    if path.suffix.lower() == ".csv":
        with open(path, encoding="utf-8-sig", newline="") as fh:
            for n, row in enumerate(csv.DictReader(fh), start=2):
                yield (n, *_csv_content(row, default_domain))
        return
    with open(path, encoding="utf-8-sig") as fh:
        for n, line in enumerate(fh, start=1):
            if line.strip():
                obj = json.loads(line)
                data = obj.get("content") or {}
                if not data.get("domain") and default_domain:
                    data["domain"] = default_domain
                yield n, obj.get("kind"), data, obj.get("id")


def import_file(con, path, source_id, progress=lambda done: None):
    path = Path(str(path).strip().strip('"'))
    if not path.is_file():
        raise FileNotFoundError(f"không thấy file {path}")
    src = con.execute("SELECT default_domain FROM sources WHERE id = ?", (source_id,)).fetchone()
    if src is None:
        raise KeyError(f"chưa đăng ký nguồn {source_id}")
    stats, errors = {"added": 0, "existing": 0, "invalid": 0}, []
    for n, kind, data, ext_id in _lines(path, src["default_domain"]):
        problems = tc.validate(kind, data)
        if problems:
            stats["invalid"] += 1
            errors.append(f"dòng {n}: {'; '.join(problems)}")
            continue
        item_id = f"{source_id}:" + (str(ext_id) if ext_id else
                                     hashlib.sha256(tc.prompt_key(data).encode("utf-8")).hexdigest()[:16])
        if con.execute("SELECT 1 FROM items WHERE id = ?", (item_id,)).fetchone():
            stats["existing"] += 1
            continue
        try:
            store.create_item(con, kind, source_id, data, f"import:{path.name}", BY, item_id=item_id)
        except ValueError as e:  # e.g. a training kind from a non-train_ok source landing in train
            stats["invalid"] += 1
            errors.append(f"dòng {n}: {e}")
            continue
        store.submit(con, item_id, BY)
        stats["added"] += 1
        progress(sum(stats.values()))
    return {**stats, "errors": errors[:200]}
