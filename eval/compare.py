"""
Paired comparison of two harness runs on the same eval set and mode: is model B really better than A?

    uv run --group eval python -m eval.compare evalset-v2 cloze lab:dense-m lab:moe-s-a3 [--bootstrap 10000]

Both runs score the same items, so the difference is measured per item and resampled per item (paired
bootstrap): item difficulty cancels out, which detects a 1–2 point gap that two independent confidence
intervals would call a tie. Reported per group (and per domain / difficulty within a group, as in the
harness summary) for acc and, in cloze mode, p_correct: mean B − A, its 95% interval, and the share of
resamples in which B does not beat A (a one-sided p-value; < 0.05 = B better beyond noise).
"""
import argparse
import json
import sys
from collections import defaultdict

import numpy as np

from pipeline.paths import DATA_ROOT

DIMS = (("lang", ""), ("domain", ""), ("difficulty", "d"))


def load(evalset, mode, model):
    path = DATA_ROOT / "eval" / "runs" / evalset / model.replace("/", "__").replace(":", "_") / f"{mode}.jsonl"
    with open(path, encoding="utf-8") as fh:
        return {r["id"]: r for r in map(json.loads, fh)}


def keys_of(record, varying):
    return [record["group"]] + [f"{record['group']}:{prefix}{record[dim]}" for dim, prefix in DIMS
                                if record.get(dim) is not None and dim in varying[record["group"]]]


def compare(a, b, bootstrap, seed=0):
    """{key: {metric: {n, a, b, diff, ci95, p_not_better}}} over the items both runs scored."""
    shared = sorted(a.keys() & b.keys())
    seen = defaultdict(lambda: defaultdict(set))
    for i in shared:
        for dim, _ in DIMS:
            seen[a[i]["group"]][dim].add(a[i].get(dim))
    varying = {g: {d for d, vals in dims.items() if len(vals) > 1} for g, dims in seen.items()}
    metrics = ["acc"] + (["p_correct"] if shared and "p_correct" in a[shared[0]] else [])
    rows = defaultdict(lambda: defaultdict(list))
    for i in shared:
        for key in keys_of(a[i], varying):
            rows[key]["acc"].append((float(a[i]["correct"]), float(b[i]["correct"])))
            if "p_correct" in metrics:
                rows[key]["p_correct"].append((a[i]["p_correct"], b[i]["p_correct"]))
    rng = np.random.default_rng(seed)
    out = {}
    for key in sorted(rows):
        out[key] = {}
        for metric, pairs in rows[key].items():
            x = np.array(pairs)
            diff = x[:, 1] - x[:, 0]
            means = np.concatenate([diff[rng.integers(0, len(diff), (min(1000, bootstrap - s), len(diff)))].mean(1)
                                    for s in range(0, bootstrap, 1000)])  # chunks bound memory on 14k items
            out[key][metric] = {"n": len(diff), "a": round(x[:, 0].mean(), 4), "b": round(x[:, 1].mean(), 4),
                                "diff": round(diff.mean(), 4),
                                "ci95": [round(float(np.quantile(means, q)), 4) for q in (0.025, 0.975)],
                                "p_not_better": round(float((means <= 0).mean()), 4)}
    return out


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("evalset")
    parser.add_argument("mode")
    parser.add_argument("model_a")
    parser.add_argument("model_b")
    parser.add_argument("--bootstrap", type=int, default=10000)
    args = parser.parse_args()
    result = compare(load(args.evalset, args.mode, args.model_a), load(args.evalset, args.mode, args.model_b),
                     args.bootstrap)
    print(f"B − A, B = {args.model_b}, A = {args.model_a}")
    for key, metrics in result.items():
        for metric, s in metrics.items():
            print(f"{key:28} {metric:9} n={s['n']:5}  A={s['a']:.3f}  B={s['b']:.3f}  diff={s['diff']:+.3f}  "
                  f"ci95=[{s['ci95'][0]:+.3f}, {s['ci95'][1]:+.3f}]  p(B<=A)={s['p_not_better']:.3f}")


if __name__ == "__main__":
    main()
