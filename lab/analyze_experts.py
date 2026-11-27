"""
Do MoE experts specialize by domain? Routes fixed held-out windows of each domain through a trained MoE
run and reports, per layer, how each domain spreads over the experts.

  LLM_DATA_ROOT=E:/llm-data uv run --group lab python -m lab.analyze_experts <run_name> [--windows 16]

Writes RUNS_DIR/<run>/experts.json and experts.md:
  share[layer][domain][expert]  fraction of that domain's routed token copies sent to the expert
  specialization[layer]         mean total-variation distance between each domain's share and the overall
                                share: 0 = every domain uses experts identically, 1 = disjoint experts
  top_tokens[expert]            most frequent input tokens per expert in the last MoE layer
"""
import argparse
import collections
import json

import torch
from tokenizers import Tokenizer

from lab import corpus, data_loader
from lab.evaluate import load_checkpoint


@torch.no_grad()
def route(model, windows, batch_size, device):
    """Per MoE layer: (tokens, experts) routing of all windows; also the flattened input tokens."""
    layers = model.moe_layers()
    for m in layers:
        m.record = []
    inputs = []
    for i in range(0, len(windows), batch_size):
        x = torch.from_numpy(windows[i:i + batch_size, :-1]).to(device)
        with torch.autocast("cuda", dtype=torch.bfloat16, enabled=device == "cuda"):
            model(x)
        inputs.append(x.flatten().cpu())
    routing = [torch.cat([r.flatten(0, -2) for r in m.record]) for m in layers]
    for m in layers:
        m.record = None
    return routing, torch.cat(inputs)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("run")
    parser.add_argument("--windows", type=int, default=16, help="held-out windows per domain")
    parser.add_argument("--top-tokens", type=int, default=12)
    args = parser.parse_args()
    device = "cuda" if torch.cuda.device_count() else "cpu"
    model, cfg, step = load_checkpoint(args.run, device)
    model.eval()
    n_layers, n_experts = len(model.moe_layers()), model.moe_layers()[0].n_experts
    tok = Tokenizer.from_file(str(corpus.TOKENIZER_PATH))

    counts = {}  # domain -> (layers, experts) routed copies
    token_hits = [collections.Counter() for _ in range(n_experts)]
    for domain in sorted(set(corpus.domain_map().values())):
        try:
            windows = data_loader.fixed_windows(corpus.TOKENS_DIR, {domain: 1.0}, cfg.seq_len, args.windows)
        except ValueError:
            continue
        routing, inputs = route(model, windows, cfg.micro_batch, device)
        counts[domain] = torch.stack([torch.bincount(r.flatten(), minlength=n_experts) for r in routing]).double()
        last = routing[-1]
        for k in range(last.shape[1]):
            for e in range(n_experts):
                token_hits[e].update(inputs[last[:, k] == e].tolist())

    total = sum(counts.values())
    overall = total / total.sum(-1, keepdim=True)
    share = {d: c / c.sum(-1, keepdim=True) for d, c in counts.items()}
    specialization = [sum(0.5 * (s[layer] - overall[layer]).abs().sum().item() for s in share.values()) / len(share)
                      for layer in range(n_layers)]
    load = (overall.max(-1).values * n_experts).tolist()  # busiest expert vs uniform, per layer
    top_tokens = [[tok.decode([t]) for t, _ in hits.most_common(args.top_tokens)] for hits in token_hits]
    report = {"run": args.run, "step": step, "specialization": specialization, "load_max_over_mean": load,
              "share": [{d: s[layer].tolist() for d, s in share.items()} for layer in range(n_layers)],
              "top_tokens_last_layer": top_tokens}
    run_dir = corpus.RUNS_DIR / args.run
    (run_dir / "experts.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")

    lines = [f"# Expert × domain: {args.run} (step {step})", "",
             "| MoE layer | specialization (0–1) | busiest expert / mean |", "|---|---|---|"]
    lines += [f"| {i} | {s:.3f} | {lo:.2f} |" for i, (s, lo) in enumerate(zip(specialization, load))]
    lines += ["", f"## Last MoE layer: share of each domain per expert (%)", "",
              "| domain | " + " | ".join(f"e{e}" for e in range(n_experts)) + " |",
              "|---|" + "---|" * n_experts]
    lines += [f"| {d} | " + " | ".join(f"{100 * v:.0f}" for v in s[-1].tolist()) + " |" for d, s in share.items()]
    lines += ["", "## Last MoE layer: most frequent input tokens per expert", ""]
    lines += [f"- e{e}: " + " · ".join(repr(t) for t in toks) for e, toks in enumerate(top_tokens)]
    (run_dir / "experts.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines[:4 + n_layers]))


if __name__ == "__main__":
    main()
