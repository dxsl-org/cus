"""
Train one lab run. Re-running the same command resumes from the run's last checkpoint.

  LLM_DATA_ROOT=E:/llm-data uv run --group lab python -u -m lab.train lab/configs/dense-s.yaml [--max-steps N]

Run directory RUNS_DIR/<name>/: config.yaml (frozen at first start; resuming with a different config is
refused), ckpt.pt (atomic replace every ckpt_minutes, at each eval and on exit), log.jsonl, and for wsd runs
ckpt-stable.pt: the state at the first decay step, kept so the stable phase can later be extended or branched.
"""
import argparse
import dataclasses
import json
import math
import os
import pathlib
import shutil
import time

import torch
import yaml
from tokenizers import Tokenizer

from lab import corpus
from lab.config import load_run
from lab.data_loader import WindowSampler
from lab.evaluate import DomainEval, samples
from lab.model import LanguageModel
from lab.optim_muon import Muon
from lab.tokenizer_train import token_byte_lengths

# MFU denominator: measured bf16 matmul peak of the RTX 4070 (phase 15 step 1); other GPUs set LAB_PEAK_FLOPS.
PEAK_FLOPS = float(os.environ.get("LAB_PEAK_FLOPS", 59e12))


def lr_factor(step, cfg):
    """Multiplier on every group's base lr. Linear warmup, then
    cosine: decay to min_lr_ratio at the last step;
    wsd:    constant, then over the last decay_fraction of steps a 1−sqrt decay to min_lr_ratio (warmup–stable–
            decay; the stable phase can be extended or branched, and anneal_mix applies during the decay)."""
    if step < cfg.warmup_steps:
        return (step + 1) / cfg.warmup_steps
    floor = cfg.min_lr_ratio
    if cfg.schedule == "wsd":
        start = cfg.decay_start
        if step < start:
            return 1.0
        progress = min(1.0, (step - start) / max(1, cfg.total_steps - start))
        return floor + (1 - floor) * (1 - math.sqrt(progress))
    progress = (step - cfg.warmup_steps) / max(1, cfg.total_steps - cfg.warmup_steps)
    return floor + (1 - floor) * 0.5 * (1 + math.cos(math.pi * min(progress, 1)))


READOUT_LIKE = ("router.weight", ".wa.weight", ".wb.weight")  # width -> a small fixed size (experts, heads)


def param_kind(name, p):
    """μP role: hidden (width × width: Muon / lr ÷ m), readout (width -> fixed: lr ÷ m), embed (fixed -> width,
    plus depthwise convs: lr unchanged, decayed), vector (gains, per-head scalars: lr unchanged, no decay)."""
    if p.dim() < 2:
        return "vector"
    if name == "embed.weight" or name.endswith("conv.weight"):
        return "embed"
    return "readout" if name.endswith(READOUT_LIKE) else "hidden"


class Optimizers:
    """Several optimizers stepped as one (Muon for hidden matrices, AdamW for the rest)."""

    def __init__(self, *opts):
        self.opts = opts

    @property
    def param_groups(self):
        return [g for o in self.opts for g in o.param_groups]

    def step(self):
        for o in self.opts:
            o.step()

    def zero_grad(self, set_to_none=True):
        for o in self.opts:
            o.zero_grad(set_to_none=set_to_none)

    def state_dict(self):
        return [o.state_dict() for o in self.opts]

    def load_state_dict(self, states):
        for o, s in zip(self.opts, states):
            o.load_state_dict(s)


def make_optimizer(model, cfg):
    """AdamW without μP keeps the original two groups (decay on matrices only), so older checkpoints resume.
    Otherwise groups follow param_kind: with mup_base_width the width multiplier m = d_model / base divides
    AdamW's lr for hidden and readout matrices; Muon (optimizer: muon) takes the hidden matrices at muon_lr,
    width-independent through its own scaling. Every group carries base_lr; lr_factor scales it each step."""
    mcfg = model.cfg
    betas = tuple(cfg.betas)
    if cfg.optimizer == "adamw" and not mcfg.mup_base_width:
        decay = [p for p in model.parameters() if p.dim() >= 2]
        no_decay = [p for p in model.parameters() if p.dim() < 2]
        opt = torch.optim.AdamW([{"params": decay, "weight_decay": cfg.weight_decay},
                                 {"params": no_decay, "weight_decay": 0.0}], lr=cfg.lr, betas=betas, fused=True)
        for g in opt.param_groups:
            g["base_lr"] = cfg.lr
        return opt
    m = mcfg.d_model / mcfg.mup_base_width if mcfg.mup_base_width else 1.0
    kinds = {"hidden": [], "readout": [], "embed": [], "vector": [], "experts": []}
    for name, p in model.named_parameters():
        kind = param_kind(name, p)
        kinds["experts" if kind == "hidden" and p.dim() == 3 else kind].append(p)
    adam = [{"params": kinds["embed"], "weight_decay": cfg.weight_decay, "base_lr": cfg.lr},
            {"params": kinds["vector"], "weight_decay": 0.0, "base_lr": cfg.lr},
            {"params": kinds["readout"], "weight_decay": cfg.weight_decay, "base_lr": cfg.lr / m}]
    if cfg.optimizer == "adamw":
        adam.append({"params": kinds["hidden"] + kinds["experts"], "weight_decay": cfg.weight_decay,
                     "base_lr": cfg.lr / m})
        return torch.optim.AdamW([g for g in adam if g["params"]], lr=cfg.lr, betas=betas, fused=True)
    muon = [{"params": kinds["hidden"], "layout": "out_in", "base_lr": cfg.muon_lr},
            {"params": kinds["experts"], "layout": "in_out", "base_lr": cfg.muon_lr}]  # MoE stores (E, in, out)
    return Optimizers(Muon([g for g in muon if g["params"]], lr=cfg.muon_lr, weight_decay=cfg.weight_decay),
                      torch.optim.AdamW([g for g in adam if g["params"]], lr=cfg.lr, betas=betas, fused=True))


def prepare_run_dir(config_path, raw):
    run_dir = corpus.RUNS_DIR / raw["train"]["name"]
    run_dir.mkdir(parents=True, exist_ok=True)
    frozen = run_dir / "config.yaml"
    if frozen.exists():
        if yaml.safe_load(frozen.read_text(encoding="utf-8")) != raw:
            raise SystemExit(f"{config_path} differs from the frozen {frozen}; use a new run name")
    else:
        frozen.write_text(pathlib.Path(config_path).read_text(encoding="utf-8"), encoding="utf-8")
    return run_dir


def save(run_dir, model, opt, step):
    tmp = run_dir / "ckpt.tmp"
    torch.save({"model": model.state_dict(), "opt": opt.state_dict(), "step": step}, tmp)
    tmp.replace(run_dir / "ckpt.pt")


def load_base_weights(model, model_cfg, base_run, device, ckpt_name="ckpt.pt"):
    """Start from another run's weights, final or pre-decay (fresh optimizer and schedule): continued pretraining,
    domain adaptation, context extension. The architecture must match; only max_seq_len may differ."""
    base_dir = corpus.RUNS_DIR / base_run
    base_cfg = load_run(base_dir / "config.yaml")[0]
    mine, theirs = dataclasses.asdict(model_cfg), dataclasses.asdict(base_cfg)
    mine.pop("max_seq_len"), theirs.pop("max_seq_len")
    if mine != theirs:
        raise SystemExit(f"init_from {base_run}: model config differs from this run's")
    state = torch.load(base_dir / ckpt_name, map_location=device, weights_only=False)
    model.load_state_dict(state["model"])
    print(f"initialized from {base_run}/{ckpt_name} step {state['step']}", flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("config")
    parser.add_argument("--max-steps", type=int, help="stop early (smoke tests); the schedule is unchanged")
    parser.add_argument("--overfit", action="store_true", help="repeat one batch: loss must approach 0")
    parser.add_argument("--tokens-dir", type=pathlib.Path, default=corpus.TOKENS_DIR)
    args = parser.parse_args()
    model_cfg, cfg, raw = load_run(args.config)
    run_dir = prepare_run_dir(args.config, raw)
    device = "cuda"
    torch.manual_seed(cfg.seed)
    torch.backends.cuda.matmul.allow_tf32 = True

    model = LanguageModel(model_cfg).to(device)
    opt = make_optimizer(model, cfg)
    step = 0
    if (run_dir / "ckpt.pt").exists():
        state = torch.load(run_dir / "ckpt.pt", map_location=device, weights_only=False)
        model.load_state_dict(state["model"])
        base_lrs = [g["base_lr"] for g in opt.param_groups]
        opt.load_state_dict(state["opt"])  # restores saved groups, which predate base_lr in older checkpoints
        for g, base in zip(opt.param_groups, base_lrs):
            g["base_lr"] = base
        step = state["step"]
        print(f"resumed {cfg.name} at step {step}", flush=True)
    elif cfg.init_from:
        load_base_weights(model, model_cfg, cfg.init_from, device, cfg.init_ckpt)
    fwd = torch.compile(model) if cfg.compile else model

    sampler = WindowSampler(args.tokens_dir, cfg.mix, cfg.seq_len, seed=cfg.seed)
    anneal = WindowSampler(args.tokens_dir, cfg.anneal_mix, cfg.seq_len, seed=cfg.seed) if cfg.anneal_mix else None
    tok = Tokenizer.from_file(str(corpus.TOKENIZER_PATH))
    evaluator = None if args.overfit else DomainEval(
        args.tokens_dir, cfg.seq_len, cfg.eval_windows, token_byte_lengths(tok), device)
    flops_per_token = model.flops_per_token(cfg.seq_len)
    print(f"{cfg.name}: {model.num_params() / 1e6:.1f}M params ({model.active_params() / 1e6:.1f}M active), "
          f"{cfg.total_steps} steps × {cfg.batch_tokens} "
          f"tokens, accum {cfg.grad_accum}", flush=True)

    log = open(run_dir / "log.jsonl", "a", encoding="utf-8")
    last_save = time.time()
    end = min(cfg.total_steps, args.max_steps or cfg.total_steps)
    try:
        while step < end:
            t0 = time.time()
            factor = lr_factor(step, cfg)
            for group in opt.param_groups:
                group["lr"] = group["base_lr"] * factor
            source = anneal if anneal and step >= cfg.decay_start else sampler
            loss_sum = 0.0
            for micro in range(cfg.grad_accum):
                key = (0, micro) if args.overfit else (step, micro)
                w = torch.from_numpy(source.batch(cfg.micro_batch, *key)).pin_memory().to(device, non_blocking=True)
                with torch.autocast("cuda", dtype=torch.bfloat16):
                    loss = fwd(w[:, :-1], w[:, 1:]) / cfg.grad_accum
                loss.backward()
                loss_sum += loss.item()
            grad_norm = torch.nn.utils.clip_grad_norm_(model.parameters(), cfg.grad_clip).item()
            opt.step()
            opt.zero_grad(set_to_none=True)
            loads = [m.balance_step() for m in model.moe_layers()]  # bias update must follow the step
            step += 1
            dt = time.time() - t0
            record = {"step": step, "loss": round(loss_sum, 4), "lr": opt.param_groups[0]["lr"],
                      "grad_norm": round(grad_norm, 3), "tok_s": round(cfg.batch_tokens / dt),
                      "mfu": round(cfg.batch_tokens / dt * flops_per_token / PEAK_FLOPS, 3)}
            if loads:
                record["moe_load_max"] = round(max(loads), 3)  # busiest expert / mean, worst layer
            if evaluator and (step % cfg.eval_every == 0 or step == end):
                record |= {k: round(v, 4) for k, v in evaluator.run(fwd, cfg.micro_batch).items()}
                record["samples"] = samples(model, tok, device)
                save(run_dir, model, opt, step)
                last_save = time.time()
            elif time.time() - last_save > cfg.ckpt_minutes * 60:
                save(run_dir, model, opt, step)
                last_save = time.time()
            if cfg.schedule == "wsd" and step == cfg.decay_start and not args.overfit:
                save(run_dir, model, opt, step)
                shutil.copyfile(run_dir / "ckpt.pt", run_dir / "ckpt-stable.pt")
                last_save = time.time()
            log.write(json.dumps(record, ensure_ascii=False) + "\n")
            log.flush()
            if step % 10 == 0 or "bpb/all" in record:
                print({k: v for k, v in record.items() if k != "samples"}, flush=True)
    finally:
        if not args.overfit:
            save(run_dir, model, opt, step)
        log.close()


if __name__ == "__main__":
    main()
