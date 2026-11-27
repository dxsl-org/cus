"""
Multi-token prediction, depth 1 (DeepSeek-V3): besides the next token t+1, predict t+2.

The module sees the main model's last hidden state at position t and the embedding of the true token t+1,
runs one extra transformer block, and reuses the shared output head. Training only: its loss is added with
weight cfg.mtp["weight"]; bpb and generation use the main head. At inference it can draft t+2 for
speculative decoding.
"""
import torch
from torch import nn

from lab.layers.norm_rope import RMSNorm

DEFAULTS = {"weight": 0.3}


class MTP(nn.Module):
    def __init__(self, cfg, block):
        super().__init__()
        self.weight = (DEFAULTS | cfg.mtp)["weight"]
        self.norm_h = RMSNorm(cfg.d_model, cfg.norm_eps)
        self.norm_e = RMSNorm(cfg.d_model, cfg.norm_eps)
        self.proj = nn.Linear(2 * cfg.d_model, cfg.d_model, bias=False)
        self.block = block
        self.norm = RMSNorm(cfg.d_model, cfg.norm_eps)

    def forward(self, hidden, next_embed, rope):
        """hidden[t] (main trunk, before its final norm) + embed(token t+1) -> normed state predicting t+2."""
        x = self.proj(torch.cat([self.norm_h(hidden), self.norm_e(next_embed)], dim=-1))
        x, _ = self.block(x, rope)
        return self.norm(x)
