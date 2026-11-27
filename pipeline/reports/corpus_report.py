"""
Summary of the canonical corpus: size, date coverage, and pre/post-LLM split per source.

    uv run python -m pipeline.reports.corpus_report      -> DATA_ROOT/reports/corpus_report.md

`docs/day ⚠` flags sources where too many docs share a publish day, which means the
"publish date" is really a crawl date (the tuoitre bug this pipeline already fixes).
"""
import collections
import sys
from datetime import date

import pyarrow.compute as pc
import pyarrow.dataset as ds

from pipeline.paths import CANONICAL_DIR, REPORTS_DIR
from pipeline.schema.document_record import LLM_CUTOFF

SUSPICIOUS_DOCS_PER_DAY = 1000
COLUMNS = ["publish_date", "publish_date_source", "era", "usage_scope"]


def source_stats(path):
    table = ds.dataset(path, format="parquet").to_table(columns=COLUMNS + ["text"])
    chars = pc.sum(pc.utf8_length(table["text"])).as_py() or 0
    words = pc.sum(pc.list_value_length(pc.utf8_split_whitespace(table["text"]))).as_py() or 0
    dates = table["publish_date"].drop_null()
    eras = collections.Counter(table["era"].to_pylist())
    tags = collections.Counter(t.split(":")[0] for t in table["publish_date_source"].to_pylist())
    distinct_days = len(pc.unique(dates)) if len(dates) else 0
    return {
        "docs": table.num_rows, "chars": chars, "words": words, "dated": len(dates),
        "min": pc.min(dates).as_py() if len(dates) else None, "max": pc.max(dates).as_py() if len(dates) else None,
        "per_day": len(dates) / distinct_days if distinct_days else 0, "eras": eras, "tags": tags,
        "blocked": pc.sum(pc.equal(table["usage_scope"], "blocked")).as_py() or 0,
    }


def _pct(part, whole):
    return f"{100 * part / whole:.1f}%" if whole else "-"


def render(stats):
    lines = [f"# Canonical corpus report ({date.today()})", "",
             f"Cutoff {LLM_CUTOFF}. `words` = whitespace tokens (≈ Vietnamese syllables).", "",
             "| source | docs | words (M) | dated | min | max | docs/day | pre_llm | post_llm | unknown | blocked | date from |",
             "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    total = collections.Counter()
    for name, s in sorted(stats.items(), key=lambda kv: -kv[1]["words"]):
        flag = " ⚠" if s["per_day"] > SUSPICIOUS_DOCS_PER_DAY else ""
        tags = ", ".join(f"{k} {v}" for k, v in s["tags"].most_common(3))
        lines.append(f"| {name} | {s['docs']} | {s['words'] / 1e6:.1f} | {_pct(s['dated'], s['docs'])} | {s['min'] or '-'} "
                     f"| {s['max'] or '-'} | {s['per_day']:.0f}{flag} | {s['eras']['pre_llm']} | {s['eras']['post_llm']} "
                     f"| {s['eras']['unknown']} | {s['blocked']} | {tags} |")
        total.update(docs=s["docs"], words=s["words"], dated=s["dated"], blocked=s["blocked"], **s["eras"])
    lines += ["", f"**Total**: {total['docs']} docs, {total['words'] / 1e9:.2f}B words, dated {_pct(total['dated'], total['docs'])}; "
                  f"pre_llm {total['pre_llm']}, post_llm {total['post_llm']}, unknown {total['unknown']}, blocked {total['blocked']}."]
    return "\n".join(lines) + "\n"


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    stats = {p.name: source_stats(p) for p in sorted(CANONICAL_DIR.iterdir())
             if p.is_dir() and not p.name.startswith(".") and any(p.rglob("*.parquet"))}
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    out = REPORTS_DIR / "corpus_report.md"
    out.write_text(render(stats), encoding="utf-8")
    print(out)
    print(render(stats).splitlines()[-1])


if __name__ == "__main__":
    main()
