"""
Per-item human review through a spreadsheet, until the Studio (phase 9) exists.

    uv run python -m taskset.review_sheet export claude_drafts [--domain law] -> <DATA_ROOT>/taskset/review/sheet-*.csv
    uv run python -m taskset.review_sheet apply SHEET.csv --reviewer anh

The CSV (UTF-8 with BOM, opens in Excel) has one row per pending item: question, options,
answer, grounding, and empty `verdict` / `comment` columns. Verdicts: approved, dropped,
needs_work (blank = not reviewed yet). `apply` records each verdict with store.review, so all
DB rules hold; a row whose item changed since export (different content sha) is skipped.
"""
import argparse
import csv
import json
import sys
from datetime import datetime

from pipeline.paths import DATA_ROOT
from taskset import store

COLUMNS = ["id", "content_sha256", "domain", "difficulty", "question", "answer", "grounding", "notes", "verdict",
           "comment"]
VERDICTS = ("approved", "dropped", "needs_work")


def _answer(data):
    if "answer_key" in data:
        return f"{data['answer_key']}. {data['choices']['ABCDEFGHIJ'.index(data['answer_key'])]}"
    ref = data.get("reference_answer")
    return " | ".join(ref) if isinstance(ref, list) else str(ref)


def export(con, source, domain=None):
    rows = con.execute("SELECT i.id, v.content_json, v.content_sha256 FROM items i "
                       "JOIN item_versions v ON v.id = i.current_version_id "
                       "WHERE i.source_id = ? AND i.status = 'pending' ORDER BY i.id", (source,)).fetchall()
    out = DATA_ROOT / "taskset" / "review" / f"sheet-{source}-{domain or 'all'}-{datetime.now():%Y%m%d-%H%M}.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with open(out, "w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, COLUMNS)
        writer.writeheader()
        for r in rows:
            data = json.loads(r["content_json"])
            if domain and data["domain"] != domain:
                continue
            meta = data.get("meta", {})
            writer.writerow({
                "id": r["id"], "content_sha256": r["content_sha256"], "domain": data["domain"],
                "difficulty": data.get("difficulty"), "question": data["messages"][-1]["content"],
                "answer": _answer(data),
                "grounding": "\n\n".join(f"{g.get('url', g.get('doc_id', ''))}\n{g.get('quote', '')}"
                                         for g in data.get("grounding", [])),
                "notes": "; ".join(f"{k}: {v}" for k, v in meta.items()), "verdict": "", "comment": ""})
            count += 1
    return out, count


def apply(con, path, reviewer):
    done, skipped, errors = 0, 0, []
    with open(path, encoding="utf-8-sig", newline="") as fh:
        for row in csv.DictReader(fh):
            verdict = (row.get("verdict") or "").strip().lower()
            if not verdict:
                skipped += 1
                continue
            if verdict not in VERDICTS:
                errors.append(f"{row['id']}: unknown verdict {verdict!r}")
                continue
            current = con.execute("SELECT v.content_sha256, i.status FROM items i "
                                  "JOIN item_versions v ON v.id = i.current_version_id WHERE i.id = ?",
                                  (row["id"],)).fetchone()
            if current is None or current["content_sha256"] != row["content_sha256"] or current["status"] != "pending":
                errors.append(f"{row['id']}: changed or no longer pending since export; re-export")
                continue
            store.review(con, row["id"], reviewer, verdict, (row.get("comment") or "").strip() or None)
            done += 1
    return done, skipped, errors


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    exp = sub.add_parser("export")
    exp.add_argument("source")
    exp.add_argument("--domain")
    app = sub.add_parser("apply")
    app.add_argument("sheet")
    app.add_argument("--reviewer", required=True)
    for p in (exp, app):
        p.add_argument("--db", default=store.DEFAULT_DB)
    args = parser.parse_args()
    con = store.connect(args.db)
    if args.command == "export":
        out, count = export(con, args.source, args.domain)
        print(f"{out}: {count} items")
        return
    done, skipped, errors = apply(con, args.sheet, args.reviewer)
    print(f"reviewed {done}, left blank {skipped}, errors {len(errors)}")
    for e in errors:
        print("  " + e)


if __name__ == "__main__":
    main()
