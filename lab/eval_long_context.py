"""
Long-context behaviour of a lab run (phase 17): what attention buys over Gated DeltaNet and back.

  LLM_DATA_ROOT=E:/llm-data uv run --group lab python -m lab.eval_long_context <run> [--lengths 2048 4096 8192]

Writes RUNS_DIR/<run>/long_context.{json,md}:
  needle    A sentence "Mật mã của kho số K là NNNN." is planted at depth 10/50/90% of a held-out haystack;
            at the end the model reads "Mật mã của kho số K là" and must rank the true code above 9 random
            codes by log-likelihood (chance = 10%). Ranking instead of generation, so small models score.
  position  bpb of held-out long documents (literature, law) by position bucket: does more context help?
  memory    prefill peak GPU memory and time per length (measured), and the decoding state a generator
            would keep per sequence (exact, from shapes): attention KV cache grows with T, GDN state does not.
  mtp       for runs with MTP: how often the t+2 draft equals the main head's next greedy token, at positions
            where the main head's greedy t+1 is the true token (there the teacher-forced pass is exactly
            the greedy one, so this is the self-speculative acceptance rate).
"""
import argparse
import json
import math
import time

import numpy as np
import torch
from tokenizers import Tokenizer

from lab import corpus, data_loader
from lab.evaluate import load_checkpoint
from lab.layers.gdn import DEFAULTS as GDN_DEFAULTS
from lab.tokenizer_train import token_byte_lengths

DEPTHS = (0.1, 0.5, 0.9)
N_CANDIDATES = 10


def haystacks(length, n, seed):
    """n held-out windows of mixed long text (no hash-identical runs across lengths)."""
    mix = {"literature": 0.4, "law": 0.3, "news": 0.3}
    return data_loader.WindowSampler(corpus.TOKENS_DIR, mix, length, split="heldout", seed=seed).batch(n, 0)


@torch.no_grad()
def candidate_logprobs(model, prefix, candidates, device):
    """Summed log-probability of each candidate continuation after the same prefix (one batch)."""
    width = max(len(c) for c in candidates)
    rows = [prefix + c + [0] * (width - len(c)) for c in candidates]
    x = torch.tensor(rows, device=device)
    with torch.autocast("cuda", dtype=torch.bfloat16, enabled=device == "cuda"):
        hidden, _ = model.trunk(x)
        logits = model.head(hidden[:, len(prefix) - 1:-1]).float()  # predictions for candidate positions
    logp = logits.log_softmax(-1).gather(-1, x[:, len(prefix):, None]).squeeze(-1)
    mask = torch.tensor([[i < len(c) for i in range(width)] for c in candidates], device=device)
    return (logp * mask).sum(-1)


def needle(model, tok, lengths, trials, device):
    rng = np.random.default_rng(0)
    out = {}
    for length in lengths:
        hay = haystacks(length, trials, seed=length)
        for depth in DEPTHS:
            hits = 0
            for t in range(trials):
                key, codes = int(rng.integers(10, 100)), rng.choice(np.arange(1000, 10000), N_CANDIDATES, replace=False)
                fact = tok.encode(f"\nMật mã của kho số {key} là {codes[0]}.\n").ids
                query = tok.encode(f"\nMật mã của kho số {key} là").ids
                body = hay[t, :length - len(fact) - len(query) - 4].tolist()
                cut = int(depth * len(body))
                prefix = body[:cut] + fact + body[cut:] + query
                cands = [tok.encode(f" {c}.").ids for c in codes]
                hits += int(candidate_logprobs(model, prefix, cands, device).argmax().item() == 0)
            out[f"{length}@{int(depth * 100)}%"] = hits / trials
    return out


@torch.no_grad()
def bpb_by_position(model, token_bytes, length, n, batch, device):
    windows = data_loader.WindowSampler(corpus.TOKENS_DIR, {"literature": 0.5, "law": 0.5}, length,
                                        split="heldout", seed=1).batch(n, 0)
    nll = torch.zeros(length, dtype=torch.float64)
    nbytes = torch.zeros(length, dtype=torch.float64)
    for i in range(0, n, batch):
        w = torch.from_numpy(windows[i:i + batch]).to(device)
        with torch.autocast("cuda", dtype=torch.bfloat16, enabled=device == "cuda"):
            loss = model(w[:, :-1], w[:, 1:], reduction="none").double()
        b = token_bytes[w[:, 1:]]
        nll += (loss * (b > 0)).sum(0).cpu()
        nbytes += b.sum(0).cpu()
    edges = [0] + [2 ** k for k in range(7, int(math.log2(length)) + 1)]
    return {f"{lo}-{hi}": nll[lo:hi].sum().item() / math.log(2) / nbytes[lo:hi].sum().item()
            for lo, hi in zip(edges, edges[1:])}


def decode_state_bytes(cfg, length):
    """Per-sequence state a generator keeps after `length` tokens: bf16 KV cache for attention layers,
    fp32 matrix state + conv tail for Gated DeltaNet layers."""
    total = 0
    conv = (GDN_DEFAULTS | (cfg.gdn or {}))["conv_size"]
    for kind in cfg.layers:
        if kind["mixer"] == "attention":
            total += 2 * cfg.n_kv_heads * cfg.head_dim * length * 2
        else:
            total += cfg.n_heads * cfg.head_dim * cfg.head_dim * 4 + 3 * cfg.d_model * (conv - 1) * 2
    return total


@torch.no_grad()
def prefill_cost(model, length, device):
    """Median of 3 prefill passes; peak GPU memory (None on CPU)."""
    x = torch.randint(1, model.cfg.vocab_size, (1, length), device=device)
    cuda = device == "cuda"
    times = []
    for _ in range(3):
        if cuda:
            torch.cuda.reset_peak_memory_stats()
            torch.cuda.synchronize()
        start = time.perf_counter()
        with torch.autocast("cuda", dtype=torch.bfloat16, enabled=cuda):
            model.head(model.trunk(x)[0][:, -1:])
        if cuda:
            torch.cuda.synchronize()
        times.append(time.perf_counter() - start)
    peak = round(torch.cuda.max_memory_allocated() / 2**20) if cuda else None
    return {"seconds": round(sorted(times)[1], 4), "peak_mb": peak}


@torch.no_grad()
def mtp_acceptance(model, length, n, batch, device):
    windows = data_loader.WindowSampler(corpus.TOKENS_DIR, {"news": 0.5, "literature": 0.5}, length,
                                        split="heldout", seed=2).batch(n, 0)
    eligible = accepted = 0
    for i in range(0, n, batch):
        w = torch.from_numpy(windows[i:i + batch]).to(device)
        with torch.autocast("cuda", dtype=torch.bfloat16, enabled=device == "cuda"):
            hidden, _ = model.trunk(w)
            greedy = model.head(hidden).argmax(-1)  # main head: token t+1 for every position t
            draft = model.mtp_logits(hidden[:, :-1], w[:, 1:]).argmax(-1)  # token t+2 given true t+1
        ok = greedy[:, :-1] == w[:, 1:]  # greedy t+1 was right, so the pass at t+1 is the greedy one
        eligible += ok.sum().item()
        accepted += (ok & (draft == greedy[:, 1:])).sum().item()
    return {"eligible_share": eligible / windows[:, 1:].size, "acceptance": accepted / max(eligible, 1)}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("run")
    parser.add_argument("--lengths", type=int, nargs="+", default=[2048, 4096, 8192])
    parser.add_argument("--trials", type=int, default=20, help="needle trials per length and depth")
    parser.add_argument("--windows", type=int, default=16, help="windows for position bpb and MTP")
    args = parser.parse_args()
    device = "cuda" if torch.cuda.device_count() else "cpu"
    longest = max(args.lengths)
    model, train_cfg, step = load_checkpoint(args.run, device, max_seq_len=longest + 64)
    model.eval()
    tok = Tokenizer.from_file(str(corpus.TOKENIZER_PATH))
    token_bytes = torch.tensor(token_byte_lengths(tok), dtype=torch.float64, device=device)
    cfg = model.cfg
    report = {"run": args.run, "step": step, "train_seq_len": train_cfg.seq_len,
              "layers": "".join(k["mixer"][0] for k in cfg.layers),
              "needle": needle(model, tok, args.lengths, args.trials, device),
              "bpb_by_position": bpb_by_position(model, token_bytes, longest, args.windows, 1, device),
              "memory": {n: {**prefill_cost(model, n, device), "decode_state_mb": decode_state_bytes(cfg, n) / 2**20}
                         for n in args.lengths}}
    if model.mtp is not None:
        report["mtp"] = mtp_acceptance(model, train_cfg.seq_len, args.windows, 4, device)
    run_dir = corpus.RUNS_DIR / args.run
    (run_dir / "long_context.json").write_text(json.dumps(report, indent=1), encoding="utf-8")

    lines = [f"# Long context: {args.run} (step {step}, trained at {train_cfg.seq_len}, layers {report['layers']})",
             "", "## Needle (accuracy, chance 10%)", "", "| length | " + " | ".join(f"{int(d * 100)}%" for d in DEPTHS) + " |",
             "|---|" + "---|" * len(DEPTHS)]
    lines += [f"| {n} | " + " | ".join(f"{report['needle'][f'{n}@{int(d * 100)}%']:.0%}" for d in DEPTHS) + " |"
              for n in args.lengths]
    lines += ["", "## bpb by position (literature + law)", "", "| tokens | bpb |", "|---|---|"]
    lines += [f"| {k} | {v:.4f} |" for k, v in report["bpb_by_position"].items()]
    lines += ["", "## Memory", "", "| length | prefill s | prefill peak MB | decode state MB |", "|---|---|---|---|"]
    lines += [f"| {n} | {m['seconds']} | {m['peak_mb']} | {m['decode_state_mb']:.2f} |" for n, m in report["memory"].items()]
    if "mtp" in report:
        lines += ["", f"## MTP draft: acceptance {report['mtp']['acceptance']:.1%} "
                      f"(on {report['mtp']['eligible_share']:.0%} of positions)"]
    (run_dir / "long_context.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
