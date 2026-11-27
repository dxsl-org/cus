"""
Bits-per-byte on fixed held-out windows, per domain (the phase-15 domain lens), plus text samples.

bpb = Σ nll(token) / ln 2 / Σ UTF-8 bytes(token), over target tokens that stand for at least one byte
(document separators are excluded). Unlike per-token loss it does not depend on the tokenizer.

  LLM_DATA_ROOT=E:/llm-data uv run --group lab python -m lab.evaluate <run_name> [--samples]
"""
import argparse
import json
import math

import torch
from tokenizers import Tokenizer

from lab import corpus, data_loader
from lab.config import load_run
from lab.model import LanguageModel
from lab.tokenizer_train import token_byte_lengths

PROMPTS = ["Theo quy định của Bộ luật Dân sự,", "Hà Nội là", "Ngày xưa, ở một làng nọ", "Để phòng bệnh cúm,"]


class DomainEval:
    def __init__(self, tokens_dir, seq_len, windows_per_domain, token_bytes, device):
        self.windows = {}
        for d in sorted(set(corpus.domain_map().values())):
            try:
                windows = data_loader.fixed_windows(tokens_dir, {d: 1.0}, seq_len, windows_per_domain)
            except ValueError:
                continue  # domain without held-out data
            self.windows[d] = torch.from_numpy(windows)
        self.token_bytes = torch.tensor(token_bytes, dtype=torch.float64, device=device)
        self.device = device

    @torch.no_grad()
    def per_window(self, model, batch_size):
        """{domain: (nll, nbytes)}: float64 arrays with one entry per window, nll in nats summed over the
        window's byte-bearing targets. Summing them gives exactly what run() reports."""
        was_training = model.training
        model.eval()
        out = {}
        for domain, windows in self.windows.items():
            nll_rows, byte_rows = [], []
            for i in range(0, len(windows), batch_size):
                w = windows[i:i + batch_size].to(self.device)
                x, y = w[:, :-1], w[:, 1:]
                with torch.autocast("cuda", dtype=torch.bfloat16, enabled=self.device == "cuda"):
                    nll = model(x, y, reduction="none")
                nbytes = self.token_bytes[y]
                nll_rows.append((nll.double().view_as(nbytes) * (nbytes > 0)).sum(1))
                byte_rows.append(nbytes.sum(1))
            out[domain] = (torch.cat(nll_rows).cpu().numpy(), torch.cat(byte_rows).cpu().numpy())
        model.train(was_training)
        return out

    def run(self, model, batch_size):
        out, nll_all, bytes_all = {}, 0.0, 0.0
        for domain, (nll, nbytes) in self.per_window(model, batch_size).items():
            out[f"bpb/{domain}"] = float(nll.sum() / math.log(2) / nbytes.sum())
            nll_all, bytes_all = nll_all + float(nll.sum()), bytes_all + float(nbytes.sum())
        out["bpb/all"] = nll_all / math.log(2) / bytes_all  # equal windows per domain: domain-balanced
        return out


def samples(model, tok, device, max_new_tokens=80, seed=0):
    torch.manual_seed(seed)
    model.eval()
    texts = []
    for prompt in PROMPTS:
        idx = torch.tensor([tok.encode(prompt).ids], device=device)
        with torch.autocast("cuda", dtype=torch.bfloat16, enabled=device == "cuda"):
            texts.append(tok.decode(model.generate(idx, max_new_tokens)[0].tolist()))
    model.train()
    return texts


def load_checkpoint(run_name, device, max_seq_len=None):
    """(model, train config, step). max_seq_len extends the RoPE tables (not stored in checkpoints) so a
    model can be evaluated beyond its training length."""
    run_dir = corpus.RUNS_DIR / run_name
    model_cfg, train_cfg, _ = load_run(run_dir / "config.yaml")
    if max_seq_len:
        model_cfg.max_seq_len = max(max_seq_len, model_cfg.max_seq_len)
    model = LanguageModel(model_cfg).to(device)
    state = torch.load(run_dir / "ckpt.pt", map_location=device, weights_only=False)
    model.load_state_dict(state["model"])
    return model, train_cfg, state["step"]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("run")
    parser.add_argument("--samples", action="store_true")
    args = parser.parse_args()
    device = "cuda"
    model, train_cfg, step = load_checkpoint(args.run, device)
    tok = Tokenizer.from_file(str(corpus.TOKENIZER_PATH))
    ev = DomainEval(corpus.TOKENS_DIR, train_cfg.seq_len, train_cfg.eval_windows, token_byte_lengths(tok), device)
    print(json.dumps({"step": step, **ev.run(model, train_cfg.micro_batch)}, indent=1))
    if args.samples:
        for text in samples(model, tok, device):
            print("---\n" + text)


if __name__ == "__main__":
    main()
