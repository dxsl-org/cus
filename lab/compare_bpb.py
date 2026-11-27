"""
Paired comparison of two lab runs in bits-per-byte: is run B really better than run A on held-out text?

    LLM_DATA_ROOT=E:/llm-data uv run --group lab python -m lab.compare_bpb <run_a> <run_b> [--windows 256]

Both runs score the same fixed held-out windows (more per domain than the training-time eval), so the
difference is measured window by window and resampled by window, stratified by domain (paired bootstrap):
how hard a window is cancels out. Reported per domain and for the domain-balanced total: bpb of A and B, the
relative change B/A − 1 in percent, its 95% interval and the share of resamples in which B is not better
(one-sided p-value). The interval covers only which held-out text was sampled. Training noise (seed: init and
data order) comes on top and is measured separately by rerunning a config with other seeds.

Per-window losses are cached next to each checkpoint (bpb-windows-<n>.npz, keyed by the checkpoint step),
so comparing one run against many others evaluates it once.
"""
import argparse
import sys

import numpy as np
import torch
from tokenizers import Tokenizer

from lab import corpus
from lab.evaluate import DomainEval, load_checkpoint
from lab.tokenizer_train import token_byte_lengths


def window_losses(run, windows, device, batch_size):
    """{domain: (nll, nbytes)} for a run's checkpoint, computed once per (checkpoint step, window count)."""
    cache = corpus.RUNS_DIR / run / f"bpb-windows-{windows}.npz"
    step = torch.load(corpus.RUNS_DIR / run / "ckpt.pt", map_location="cpu", weights_only=False,
                      mmap=True)["step"]
    if cache.exists():
        z = np.load(cache)
        if int(z["step"]) == step:
            return {k[4:]: (z[k], z["bytes" + k[3:]]) for k in z.files if k.startswith("nll/")}, z["seq_len"]
    model, train_cfg, step = load_checkpoint(run, device)
    tok = Tokenizer.from_file(str(corpus.TOKENIZER_PATH))
    ev = DomainEval(corpus.TOKENS_DIR, train_cfg.seq_len, windows, token_byte_lengths(tok), device)
    losses = ev.per_window(model, batch_size)
    arrays = {f"nll/{d}": v[0] for d, v in losses.items()} | {f"bytes/{d}": v[1] for d, v in losses.items()}
    np.savez(cache, step=step, seq_len=train_cfg.seq_len, **arrays)
    del model
    if device == "cuda":
        torch.cuda.empty_cache()
    return losses, train_cfg.seq_len


def bpb(nll_total, bytes_total):
    return nll_total / np.log(2) / bytes_total


def compare(a, b, bootstrap, seed=0):
    """{domain | "all": {a, b, diff_pct, ci95, p_not_better}}; resampling is stratified by domain and shared
    by A and B, and "all" pools the resampled domains exactly as DomainEval.run does."""
    rng = np.random.default_rng(seed)
    out = {}
    pooled = {"a": np.zeros(2), "b": np.zeros(2)}
    boot = {"a": np.zeros((2, bootstrap)), "b": np.zeros((2, bootstrap))}
    for domain in sorted(a.keys() & b.keys()):
        if not np.array_equal(a[domain][1], b[domain][1]):
            raise ValueError(f"{domain}: the runs saw different windows (tokenizer or seq_len differ)")
        n = len(a[domain][0])
        idx = rng.integers(0, n, (bootstrap, n))
        point, res = {}, {}
        for side, (nll, nbytes) in (("a", a[domain]), ("b", b[domain])):
            point[side] = np.array([nll.sum(), nbytes.sum()])
            res[side] = np.stack([nll[idx].sum(1), nbytes[idx].sum(1)])
            pooled[side] += point[side]
            boot[side] += res[side]
        out[domain] = summarize(bpb(*point["a"]), bpb(*point["b"]), bpb(*res["a"]), bpb(*res["b"]))
    out["all"] = summarize(bpb(*pooled["a"]), bpb(*pooled["b"]), bpb(*boot["a"]), bpb(*boot["b"]))
    return out


def summarize(a, b, boot_a, boot_b):
    rel = (boot_b / boot_a - 1) * 100
    return {"a": round(float(a), 4), "b": round(float(b), 4), "diff_pct": round(float((b / a - 1) * 100), 3),
            "ci95": [round(float(np.quantile(rel, q)), 3) for q in (0.025, 0.975)],
            "p_not_better": round(float((rel >= 0).mean()), 4)}


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("run_a")
    parser.add_argument("run_b")
    parser.add_argument("--windows", type=int, default=256, help="held-out windows per domain")
    parser.add_argument("--bootstrap", type=int, default=10000)
    parser.add_argument("--batch-size", type=int, default=8)
    parser.add_argument("--device", default="cuda" if torch.cuda.is_available() else "cpu")
    args = parser.parse_args()
    (a, len_a), (b, len_b) = (window_losses(r, args.windows, args.device, args.batch_size)
                              for r in (args.run_a, args.run_b))
    if len_a != len_b:
        raise SystemExit(f"seq_len differs ({len_a} vs {len_b}): bpb over different windows is not paired")
    print(f"B/A − 1 in %, A = {args.run_a}, B = {args.run_b}, {args.windows} windows per domain")
    for key, s in compare(a, b, args.bootstrap).items():
        print(f"{key:16} A={s['a']:.4f}  B={s['b']:.4f}  diff={s['diff_pct']:+.2f}%  "
              f"ci95=[{s['ci95'][0]:+.2f}, {s['ci95'][1]:+.2f}]  p(B>=A)={s['p_not_better']:.3f}")


if __name__ == "__main__":
    main()
