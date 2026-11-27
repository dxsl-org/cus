"""
Spot-check sample and bulk approval for imported public benchmarks.

    uv run python -m taskset.bulk_review sample vmlu viquad2 ... [--per-source 50]
    uv run python -m taskset.bulk_review approve vmlu ... --reviewer anh --sample-file <path>

`sample` writes a Markdown file of hash-chosen pending items per source for a human to read.
`approve` is the human's decision applied in bulk, after reading that sample: each pending
item of the sources is first re-checked (its own reference answer must pass its verifier,
recorded as a verifier run), then gets a review `approved` naming the reviewer and the sample
file's sha256. Items whose reference fails are left pending and listed. Never use this for
newly authored items: those are reviewed one by one.
"""
import argparse
import hashlib
import json
import sys
from datetime import date

from pipeline.paths import DATA_ROOT
from taskset import store

SAMPLE_SALT = "llm-taskset/review-sample/v1"


def _pending(con, source):
    return con.execute("SELECT i.id, v.content_json FROM items i JOIN item_versions v ON v.id = i.current_version_id "
                       "WHERE i.source_id = ? AND i.status = 'pending' ORDER BY i.id", (source,)).fetchall()


def _reference(data):
    return data["answer_key"] if "answer_key" in data else data.get("reference_answer")


def _reference_candidate(data):
    """One answer to run through the verifier: the key, or the first of several accepted answers."""
    ref = _reference(data)
    return ref[0] if isinstance(ref, list) else ref


def _render(item_id, data):
    lines = [f"### `{item_id}`", "", "````text", data["messages"][-1]["content"].strip(), "````"]
    ref = _reference(data)
    if "answer_key" in data:
        lines.append(f"**Đáp án:** {ref}. {data['choices']['ABCDEFGHIJ'.index(ref)]}")
    elif isinstance(ref, list):
        lines.append("**Đáp án chấp nhận:** " + " | ".join(ref))
    else:
        lines += ["**Lời giải mẫu:**", "```python", str(ref).strip(), "```"]
    return "\n".join(lines + [""])


def sample(con, sources, per_source):
    out = DATA_ROOT / "taskset" / "review" / f"sample-{date.today():%Y%m%d}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    parts = ["# Mẫu kiểm tra trước khi duyệt hàng loạt", ""]
    for source in sources:
        rows = sorted(_pending(con, source),
                      key=lambda r: hashlib.sha256(f"{SAMPLE_SALT}:{r['id']}".encode()).hexdigest())[:per_source]
        parts += [f"## {source} ({len(rows)} mẫu / {len(_pending(con, source))} chờ duyệt)", ""]
        parts += [_render(r["id"], json.loads(r["content_json"])) for r in rows]
    out.write_text("\n".join(parts), encoding="utf-8")
    return out


def approve(con, sources, reviewer, sample_file):
    digest = hashlib.sha256(open(sample_file, "rb").read()).hexdigest()
    comment = f"bulk approval after spot-check sample {sample_file} sha256={digest}"
    result = {}
    for source in sources:
        approved, failed = 0, []
        for row in _pending(con, source):
            passed, detail = store.run_verifier(con, row["id"], _reference_candidate(json.loads(row["content_json"])),
                                                "reference")
            if not passed:
                failed.append((row["id"], detail))
                continue
            store.review(con, row["id"], reviewer, "approved", comment)
            approved += 1
        result[source] = (approved, failed)
    return result


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["sample", "approve"])
    parser.add_argument("sources", nargs="+")
    parser.add_argument("--db", default=store.DEFAULT_DB)
    parser.add_argument("--per-source", type=int, default=50)
    parser.add_argument("--reviewer")
    parser.add_argument("--sample-file")
    args = parser.parse_args()
    con = store.connect(args.db)
    if args.command == "sample":
        print(sample(con, args.sources, args.per_source))
        return
    if not args.reviewer or not args.sample_file:
        parser.error("approve needs --reviewer and --sample-file (the sample the reviewer read)")
    for source, (approved, failed) in approve(con, args.sources, args.reviewer, args.sample_file).items():
        print(f"{source}: approved {approved}, reference failed {len(failed)}")
        for item_id, detail in failed:
            print(f"  {item_id}: {detail}")


if __name__ == "__main__":
    main()
