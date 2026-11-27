"""
Import authored eval questions (drafted by a model or a person) as pending holdout items.

    uv run python -m taskset.import_drafts FILE.jsonl --origin model:claude-opus-5-5 [--check]

One JSON object per line: {"kind": "eval_mcq" | "eval_open" | ..., "content": {...TaskItem content}}.
Every line must validate (taskset.content) and its own reference answer (answer_key, or the
first reference_answer) must pass its verifier; failing lines are reported and not imported.
--check validates only, without touching the store. Items go to split `holdout` from source
`claude_drafts` (usage_scope blocked: never trained on), status pending: a human approves each.
Item id = "claude_drafts:" + hash of the user turns, so the same question is never added twice.
"""
import argparse
import hashlib
import json
import sys

from taskset import content as tc
from taskset import store, verifiers

SOURCE = "claude_drafts"


def check_line(obj):
    """Errors for one draft line (empty when importable)."""
    kind, data = obj.get("kind"), obj.get("content") or {}
    if kind not in tc.EVAL_KINDS:
        return [f"kind must be one of {sorted(tc.EVAL_KINDS)}"]
    errors = tc.validate(kind, data)
    if errors:
        return errors
    if not data.get("verifier"):
        return ["authored eval items need a verifier"]
    ref = data.get("answer_key") if kind == "eval_mcq" else data.get("reference_answer")
    passed, detail = verifiers.check(data["verifier"], ref[0] if isinstance(ref, list) else ref, data)
    return [] if passed else [f"reference fails its own verifier: {detail}"]


def item_id(data):
    return f"{SOURCE}:" + hashlib.sha256(tc.prompt_key(data).encode("utf-8")).hexdigest()[:16]


def run(path, origin, check_only, db=store.DEFAULT_DB):
    lines = [x for x in open(path, encoding="utf-8").read().splitlines() if x.strip()]
    good, bad, seen = [], [], set()
    for n, line in enumerate(lines, 1):
        try:
            obj = json.loads(line)
        except json.JSONDecodeError as exc:
            bad.append((n, [f"invalid JSON: {exc}"]))
            continue
        errors = check_line(obj)
        key = item_id(obj["content"]) if not errors else None
        if key in seen:
            errors = ["duplicate question in this file"]
        if errors:
            bad.append((n, errors))
            continue
        seen.add(key)
        good.append((key, obj))
    added = 0
    if not check_only and good:
        con = store.connect(db)
        store.add_source(con, SOURCE, "internal (authored)", "blocked",
                         note="newly written eval questions; never published, never trained on")
        existing = {r[0] for r in con.execute("SELECT id FROM items WHERE source_id = ?", (SOURCE,))}
        for key, obj in good:
            if key in existing:
                continue
            store.create_item(con, obj["kind"], SOURCE, obj["content"], origin, origin, split="holdout",
                              item_id=key)
            store.submit(con, key, origin)
            added += 1
    return len(good), bad, added


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("files", nargs="+")
    parser.add_argument("--origin", required=True, help="model:<name@version> or human:<user>")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--db", default=store.DEFAULT_DB)
    args = parser.parse_args()
    status = 0
    for path in args.files:
        ok, bad, added = run(path, args.origin, args.check, args.db)
        print(f"{path}: {ok} valid, {len(bad)} rejected" + ("" if args.check else f", {added} added"))
        for n, errors in bad:
            print(f"  line {n}: {'; '.join(errors)}")
        status |= bool(bad)
    sys.exit(status)


if __name__ == "__main__":
    main()
