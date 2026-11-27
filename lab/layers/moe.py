"""
Mixture-of-Experts FFN: a router sends each token to its top-k of n_experts small SwiGLU experts, plus
n_shared experts every token always uses (DeepSeekMoE). Dropless: no capacity limit, no token is skipped.

Load balancing (cfg.moe["balance"]):
  aux  — Switch-style loss E·Σ f_i·P_i (f = share of routed tokens, P = mean router probability), returned
         to the model and added to the training loss.
  bias — DeepSeek-V3 style, no extra loss: a per-expert bias is added to the scores used for *choosing*
         experts (not to the mixing weights) and nudged after each optimizer step by balance_step().

Dispatch sorts token copies by expert, then runs every expert at once (cfg.moe["dispatch"]):
  grouped — torch._grouped_mm over the sorted copies, one call per weight matrix; group sizes stay on the
            GPU (no host sync), so the layer compiles with the rest of the model. CUDA only.
  loop    — one matmul per expert; needs the group sizes on the host, so it runs eagerly. CPU fallback and
            the reference the grouped path is tested against.
  auto    — grouped on CUDA, loop elsewhere. Both compute the same function (same routing, dropless).
"""
import math

import torch
import torch.nn.functional as F
from torch import nn

from lab.layers.mlp import SwiGLU

DEFAULTS = {"n_experts": 8, "top_k": 2, "n_shared": 0, "expert_hidden": None, "gate": "softmax",
            "balance": "aux", "aux_coef": 0.01, "z_coef": 1e-3, "bias_rate": 1e-3, "dense_first_layers": 0,
            "dispatch": "auto"}


class MoE(nn.Module):
    def __init__(self, cfg):
        super().__init__()
        m = DEFAULTS | (cfg.moe or {})
        if (m["balance"] not in ("aux", "bias") or m["gate"] not in ("softmax", "sigmoid")
                or m["dispatch"] not in ("auto", "grouped", "loop")):
            raise ValueError(f"bad moe config: {m}")
        self.dispatch = m["dispatch"]
        self.n_experts, self.top_k, self.gate, self.balance = m["n_experts"], m["top_k"], m["gate"], m["balance"]
        self.aux_coef, self.z_coef, self.bias_rate = m["aux_coef"], m["z_coef"], m["bias_rate"]
        d, hidden = cfg.d_model, m["expert_hidden"] or cfg.ffn_hidden
        self.router = nn.Linear(d, self.n_experts, bias=False)
        # Experts stored as stacked matrices (expert, in, out) so one parameter holds all of them.
        std_in, std_out = 0.02 * cfg.init_scale, 0.02 / math.sqrt(2 * cfg.n_layers) * cfg.init_scale
        self.w1 = nn.Parameter(torch.randn(self.n_experts, d, hidden) * std_in)
        self.w3 = nn.Parameter(torch.randn(self.n_experts, d, hidden) * std_in)
        self.w2 = nn.Parameter(torch.randn(self.n_experts, hidden, d) * std_out)
        self.shared = SwiGLU(cfg, hidden * m["n_shared"]) if m["n_shared"] else None
        self.expert_params = 3 * d * hidden
        self.register_buffer("bias", torch.zeros(self.n_experts))
        self.register_buffer("load", torch.zeros(self.n_experts))  # routed copies since last balance_step
        self.record = None  # analyze_experts sets a list here to collect routing decisions

    def forward(self, x):
        """(y, aux_loss) for x of shape (B, T, d)."""
        shape = x.shape
        flat = x.reshape(-1, shape[-1])
        logits = self.router(flat).float()
        scores = logits.softmax(-1) if self.gate == "softmax" else logits.sigmoid()
        choice = scores + self.bias if self.balance == "bias" else scores
        idx = choice.topk(self.top_k, dim=-1).indices  # (N, k)
        weights = scores.gather(1, idx)
        weights = weights / weights.sum(-1, keepdim=True)  # mixing weights of the chosen experts sum to 1

        flat_idx = idx.flatten()
        order = flat_idx.argsort(stable=True)
        token_of = order // self.top_k  # which token each sorted copy belongs to
        counts = flat_idx.new_zeros(self.n_experts).scatter_add_(0, flat_idx, torch.ones_like(flat_idx))
        grouped = self.dispatch == "grouped" or (self.dispatch == "auto" and x.is_cuda)
        out = (self._grouped if grouped else self._loop)(flat[token_of], counts)
        routed = out * weights.flatten()[order].unsqueeze(-1).to(out.dtype)
        y = torch.zeros_like(flat, dtype=routed.dtype).index_add_(0, token_of, routed).to(x.dtype)
        if self.shared is not None:
            y = y + self.shared(flat)

        if self.training:
            with torch.no_grad():
                self.load += counts
        if self.record is not None:
            self.record.append(idx.view(*shape[:-1], self.top_k).detach().cpu())
        aux = self.z_coef * torch.logsumexp(logits, -1).pow(2).mean()  # keeps router logits small
        if self.balance == "aux":
            share = counts.float() / flat_idx.numel()
            probs = scores / scores.sum(-1, keepdim=True)
            aux = aux + self.aux_coef * self.n_experts * (share * probs.mean(0)).sum()
        return y.view(shape), aux

    def _grouped(self, xs, counts):
        """Expert SwiGLU on copies sorted by expert, `counts` copies per expert, in bf16 (as under autocast)."""
        offs = counts.cumsum(0).to(torch.int32)
        xs = xs.to(torch.bfloat16)
        w1, w3, w2 = (w.to(torch.bfloat16) for w in (self.w1, self.w3, self.w2))
        hidden = F.silu(torch._grouped_mm(xs, w1, offs=offs)) * torch._grouped_mm(xs, w3, offs=offs)
        return torch._grouped_mm(hidden, w2, offs=offs)

    @torch.compiler.disable
    def _loop(self, xs, counts):
        outs = [(F.silu(chunk @ self.w1[e]) * (chunk @ self.w3[e])) @ self.w2[e]
                for e, chunk in enumerate(xs.split(counts.tolist())) if len(chunk)]
        return torch.cat(outs)

    @torch.no_grad()
    def balance_step(self):
        """After an optimizer step: update the bias (bias mode), return max/mean load, reset counts."""
        load = self.load.clone()
        if self.balance == "bias":
            self.bias += self.bias_rate * torch.sign(load.mean() - load)
        self.load.zero_()
        return (load.max() / load.mean().clamp(min=1)).item()
