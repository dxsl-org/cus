"""
Pick the sweep run with the lowest final bpb/all and write its lr into a run config.

  uv run --group lab python -m lab.pick_lr lab/configs/dense-s.yaml dense-s-lr1.5e-3 dense-s-lr7.5e-4 ...

Exits non-zero if any run has no eval yet, so a chain never launches with a half-finished sweep.
"""
import argparse
import json
import re
import pathlib

from lab import corpus
from lab.config import load_run


def final_bpb(run_name):
    rows = [json.loads(line) for line in open(corpus.RUNS_DIR / run_name / "log.jsonl", encoding="utf-8")]
    evals = [r for r in rows if "bpb/all" in r]
    if not evals:
        raise SystemExit(f"{run_name} has no eval yet")
    return evals[-1]["bpb/all"]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("config", type=pathlib.Path)
    parser.add_argument("runs", nargs="+")
    args = parser.parse_args()
    scores = {run: final_bpb(run) for run in args.runs}
    best = min(scores, key=scores.get)
    lr = load_run(corpus.RUNS_DIR / best / "config.yaml")[1].lr
    text = args.config.read_text(encoding="utf-8")
    args.config.write_text(re.sub(r"(?m)^  lr: .*$", f"  lr: {lr:g}", text), encoding="utf-8")
    print(json.dumps({"bpb_all": scores, "best": best, "lr": lr}))


if __name__ == "__main__":
    main()
