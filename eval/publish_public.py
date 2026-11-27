"""
Publish the public part of the frozen eval sets to the Hugging Face dataset DXSL/cus-eval-public.

  LLM_DATA_ROOT=E:/llm-data uv run python -m eval.publish_public [--dry-run] [--repo DXSL/cus-eval-public]

An item is published only when it is not in the holdout split AND its source dataset has a known open
license (manifests/datasets/<source>.yaml): holdout items are the internal yardstick and must never be seen by
a model trained on public data; items from mirrors of unknown license (ViQuAD2, VMLU) stay local. Items are
written exactly as the harness scores them (prompt, choices, answer key, cloze template, verifier), each with
its source and license, so published scores (DXSL/cus-lab-runs) can be reproduced by id. Token: see
pipeline.hf_hub.
"""
import argparse
import json
import pathlib
import tempfile
from collections import Counter

import yaml

from pipeline.hf_hub import folder_size, git_commit, render_card, upload_dataset
from pipeline.paths import DATA_ROOT, ROOT

FROZEN_EVALSETS = ("evalset-v1", "evalset-v2")
CARD = pathlib.Path(__file__).parent / "hf" / "public-eval-card.md"


def source_license(source):
    """License of an item's source dataset; None when the source has no dataset manifest (self-authored items)."""
    path = ROOT / "manifests" / "datasets" / f"{source}.yaml"
    return yaml.safe_load(path.read_text(encoding="utf-8"))["license"] if path.is_file() else None


def publishable(license_):
    return bool(license_) and not license_.startswith("unknown")


def stage(out):
    licenses, counts, kept = {}, {}, {}
    (out / "data").mkdir()
    for evalset in FROZEN_EVALSETS:
        n = Counter()
        with open(DATA_ROOT / "eval" / evalset / "items.jsonl", encoding="utf-8") as f, \
                open(out / "data" / f"{evalset}.jsonl", "w", encoding="utf-8") as g:
            for line in f:
                item = json.loads(line)
                source = item["id"].split(":", 1)[0]
                license_ = licenses.setdefault(source, source_license(source))
                if item["split"] == "holdout" or not publishable(license_):
                    n["withheld"] += 1
                    continue
                g.write(json.dumps({**item, "source": source, "license": license_}, ensure_ascii=False) + "\n")
                n["published"] += 1
                kept.setdefault((source, license_), set()).add(item["id"])
        counts[evalset] = dict(n)
    return counts, {k: len(ids) for k, ids in kept.items()}


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--repo", default="DXSL/cus-eval-public")
    parser.add_argument("--dry-run", action="store_true", help="stage into a temp dir and print the plan only")
    args = parser.parse_args()
    with tempfile.TemporaryDirectory() as tmp:
        out = pathlib.Path(tmp)
        counts, kept = stage(out)
        commit = git_commit()
        sources = "\n".join(f"| `{s}` | {lic} | {n} |" for (s, lic), n in sorted(kept.items()))
        totals = "\n".join(f"| `{e}` | {c.get('published', 0)} | {c.get('withheld', 0)} |" for e, c in counts.items())
        render_card(CARD, out, commit=commit, sources=sources, totals=totals)
        print(counts, dict(kept), f"{folder_size(out) / 1e6:.1f} MB")
        if not args.dry_run:
            print(upload_dataset(args.repo, out, ["data/**"], f"Sync public eval items from dxsl-org/cus@{commit[:7]}"))


if __name__ == "__main__":
    main()
