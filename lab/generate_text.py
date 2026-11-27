"""
Try lab models by hand: type a prompt, every loaded model continues it in turn (streamed).

These are base models (no instruction tuning): write the beginning of a text, not a question.
Runs on the CPU by default so it does not compete with a training job for VRAM; --device cuda if the GPU is free.

  LLM_DATA_ROOT=E:/llm-data uv run --group lab python -m lab.generate_text [run ...] [--prompt TEXT]
  default runs: the best run of each size tier by held-out bpb: combo-s-recipe (S) and combo-m (M);
  default runs without a checkpoint yet are skipped. Pass runs explicitly to try others (e.g. combo-m-law-l1).

Sampling: temperature + top-k + top-p, with a repetition penalty (divides positive logits / multiplies negative
ones of tokens already in the recent context, as in CTRL) because these models loop easily.
"""
import argparse
import json
import sys
import time

import torch
import torch.nn.functional as F
from tokenizers import Tokenizer

from lab import corpus
from lab.evaluate import load_checkpoint

DEFAULT_RUNS = ["combo-s-recipe", "combo-m"]  # best of each size tier by held-out bpb/all
PENALTY_WINDOW = 256  # tokens looked back for the repetition penalty


def sample_next(logits, history, args):
    logits = logits.float()
    if args.repetition_penalty != 1.0 and len(history):  # history = generated tokens only, not the prompt
        seen = torch.unique(history[-PENALTY_WINDOW:])
        picked = logits[seen]
        logits[seen] = torch.where(picked > 0, picked / args.repetition_penalty, picked * args.repetition_penalty)
    if args.temperature <= 0:
        return int(logits.argmax())
    logits = logits / args.temperature
    if args.top_k:
        kth = torch.topk(logits, min(args.top_k, logits.numel())).values[-1]
        logits = logits.masked_fill(logits < kth, float("-inf"))
    probs = F.softmax(logits, -1)
    if args.top_p < 1.0:
        sorted_p, order = probs.sort(descending=True)
        drop = sorted_p.cumsum(0) - sorted_p > args.top_p  # keep the smallest prefix whose mass reaches top_p
        probs[order[drop]] = 0
        probs = probs / probs.sum()
    return int(torch.multinomial(probs, 1))


@torch.no_grad()
def continue_text(model, tok, prompt, eot_id, args, device):
    """Streams the continuation to stdout. Every step re-runs the context (no KV cache): fine at lab scale."""
    ids = [eot_id] + tok.encode(prompt).ids  # documents are separated by EOT in training, so start a new one
    n_prompt = len(ids)
    shown = ""
    start = time.perf_counter()
    for _ in range(args.max_new_tokens):
        idx = torch.tensor([ids[-model.cfg.max_seq_len:]], device=device)
        with torch.autocast("cuda", dtype=torch.bfloat16, enabled=device == "cuda"):
            logits = model(idx)[0, -1]
        nxt = sample_next(logits, torch.tensor(ids[n_prompt:], dtype=torch.long, device=device), args)
        if nxt == eot_id:
            break
        ids.append(nxt)
        text = tok.decode(ids[n_prompt:])
        if not text.endswith("\ufffd"):  # a multi-byte character can span tokens; wait until it is complete
            print(text[len(shown):], end="", flush=True)
            shown = text
    n_new = len(ids) - n_prompt
    print(f"\n  [{n_new} tokens, {n_new / (time.perf_counter() - start):.1f} tok/s]")


def setup_console():
    if sys.platform == "win32":  # PowerShell 5.1 defaults to a legacy code page that mangles Vietnamese
        import ctypes
        ctypes.windll.kernel32.SetConsoleCP(65001)
        ctypes.windll.kernel32.SetConsoleOutputCP(65001)
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stdin.reconfigure(encoding="utf-8")


def describe(model, run):
    """One line: architecture, params, final held-out bpb (from the run log)."""
    cfg = model.cfg
    parts = []
    if cfg.gdn:
        every = cfg.gdn.get("attention_every", 0)
        parts.append(f"GDN {every - 1}:1 attention" if every else "GDN only")
    else:
        parts.append("attention")
    if cfg.attn_gate:
        parts.append("gated attn")
    parts.append(f"MoE {cfg.moe['n_experts']}x top-{cfg.moe['top_k']}" if cfg.moe else "dense FFN")
    if cfg.mtp:
        parts.append("MTP")
    bpb = None
    log = corpus.RUNS_DIR / run / "log.jsonl"
    if log.exists():
        evals = [r for r in map(json.loads, log.read_text(encoding="utf-8").splitlines()) if "bpb/all" in r]
        bpb = evals[-1]["bpb/all"] if evals else None
    total, active = model.num_params() / 1e6, model.active_params() / 1e6
    size = f"{total:.0f}M params" if abs(total - active) < 0.5 else f"{total:.0f}M params, {active:.0f}M active"
    return f"{', '.join(parts)}; {size}; held-out bpb {bpb}"


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("runs", nargs="*", default=DEFAULT_RUNS)
    parser.add_argument("--prompt", help="generate once and exit instead of the interactive loop")
    parser.add_argument("--max-new-tokens", type=int, default=150)
    parser.add_argument("--temperature", type=float, default=0.8, help="0 = greedy")
    parser.add_argument("--top-k", type=int, default=50, help="0 = off")
    parser.add_argument("--top-p", type=float, default=0.95)
    parser.add_argument("--repetition-penalty", type=float, default=1.2, help="1.0 = off")
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument("--device", default="cpu", choices=["cpu", "cuda"])
    args = parser.parse_args()
    setup_console()

    tok = Tokenizer.from_file(str(corpus.TOKENIZER_PATH))
    eot_id = tok.token_to_id(corpus.EOT)
    models = []
    explicit = args.runs != DEFAULT_RUNS
    for run in args.runs:
        if not explicit and not (corpus.RUNS_DIR / run / "ckpt.pt").exists():
            print(f"skipped {run}: no checkpoint yet")
            continue
        model, _, step = load_checkpoint(run, args.device)
        model.eval()
        print(f"loaded {run} (step {step}): {describe(model, run)}")
        models.append((run, model))

    prompts = [args.prompt] if args.prompt else None
    if prompts is None:
        print("Type the beginning of a text (Enter on an empty line quits).")
    while True:
        prompt = prompts.pop() if prompts else (None if args.prompt else input("\n> ").strip())
        if not prompt:
            break
        for run, model in models:
            if args.seed is not None:
                torch.manual_seed(args.seed)
            print(f"\n=== {run}\n{prompt}", end="", flush=True)
            continue_text(model, tok, prompt, eot_id, args, args.device)


if __name__ == "__main__":
    main()
