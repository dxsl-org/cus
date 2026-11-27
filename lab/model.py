"""
Decoder-only language model assembled from per-layer block types (ModelConfig.layers).

Block: x += mixer(norm(x)); x += ffn(norm(x)) (pre-norm residual). Output head shares the embedding matrix.
An FFN may also return an auxiliary loss (MoE load balancing); it is added to the mean training loss only,
as is the multi-token-prediction loss when cfg.mtp is set. Evaluation (reduction='none') sees neither.
"""
import math

import torch
import torch.nn.functional as F
from torch import nn

from lab.layers.attention import Attention
from lab.layers.gdn import DEFAULTS as GDN_DEFAULTS
from lab.layers.gdn import GatedDeltaNet
from lab.layers.mlp import SwiGLU
from lab.layers.moe import MoE
from lab.layers.mtp import MTP
from lab.layers.norm_rope import RMSNorm, rope_tables

MIXERS = {"attention": Attention, "gdn": GatedDeltaNet}
FFNS = {"dense": SwiGLU, "moe": MoE}


class Block(nn.Module):
    def __init__(self, cfg, kind):
        super().__init__()
        self.norm1 = RMSNorm(cfg.d_model, cfg.norm_eps)
        self.mixer = MIXERS[kind["mixer"]](cfg)
        self.norm2 = RMSNorm(cfg.d_model, cfg.norm_eps)
        self.ffn = FFNS[kind["ffn"]](cfg)

    def forward(self, x, rope):
        """(x, aux_loss); aux is 0 for FFNs without one."""
        x = x + self.mixer(self.norm1(x), rope)
        out = self.ffn(self.norm2(x))
        out, aux = out if isinstance(out, tuple) else (out, 0.0)
        return x + out, aux


class LanguageModel(nn.Module):
    def __init__(self, cfg):
        super().__init__()
        self.cfg = cfg
        self.embed = nn.Embedding(cfg.vocab_size, cfg.d_model)
        self.blocks = nn.ModuleList(Block(cfg, kind) for kind in cfg.layers)
        self.norm = RMSNorm(cfg.d_model, cfg.norm_eps)
        self.mtp = MTP(cfg, Block(cfg, {"mixer": "attention", "ffn": "dense"})) if cfg.mtp else None
        cos, sin = rope_tables(cfg.head_dim, cfg.max_seq_len, cfg.rope_theta)
        self.register_buffer("rope_cos", cos, persistent=False)
        self.register_buffer("rope_sin", sin, persistent=False)
        self.apply(self._init)
        # μP readout with tied embeddings: logits × base/width (1 without μP or at the tuned width)
        self.readout_scale = cfg.mup_base_width / cfg.d_model if cfg.mup_base_width else 1.0

    def _init(self, module):
        # GPT-2 scheme: N(0, 0.02); projections that write into the residual stream are shrunk by
        # 1/sqrt(2·layers) so the residual variance does not grow with depth. μP: hidden matrices ×
        # sqrt(base/d_model), i.e. std ∝ 1/sqrt(fan_in) around the tuned width.
        if isinstance(module, nn.Linear):
            std = 0.02 / math.sqrt(2 * self.cfg.n_layers) if getattr(module, "residual_out", False) else 0.02
            nn.init.normal_(module.weight, std=std * self.cfg.init_scale)
        elif isinstance(module, nn.Embedding):
            nn.init.normal_(module.weight, std=0.02)

    def trunk(self, idx):
        """(hidden before the final norm, summed aux loss): everything up to the output head."""
        x = self.embed(idx)
        rope = (self.rope_cos, self.rope_sin)
        aux = 0.0
        for block in self.blocks:
            x, block_aux = block(x, rope)
            aux = aux + block_aux
        return x, aux

    def head(self, hidden):
        return F.linear(self.norm(hidden), self.embed.weight) * self.readout_scale

    def mtp_logits(self, hidden, next_tokens):
        """Logits for token t+2 from hidden[t] and the given token t+1 (requires cfg.mtp)."""
        rope = (self.rope_cos, self.rope_sin)
        return F.linear(self.mtp(hidden, self.embed(next_tokens), rope), self.embed.weight) * self.readout_scale

    def forward(self, idx, targets=None, reduction="mean"):
        """Logits (B, T, V) without targets; else the cross-entropy, per token when reduction='none'."""
        x, aux = self.trunk(idx)
        logits = self.head(x)
        if targets is None:
            return logits
        loss = F.cross_entropy(logits.float().flatten(0, 1), targets.flatten(), reduction=reduction)
        if reduction == "none":
            return loss.view(targets.shape)
        if not self.training:
            return loss
        if self.mtp is not None:
            # Position t sees token t+1 = targets[t] and predicts token t+2 = targets[t+1].
            mtp_logits = self.mtp_logits(x[:, :-1], targets[:, :-1])
            loss = loss + self.mtp.weight * F.cross_entropy(mtp_logits.float().flatten(0, 1),
                                                            targets[:, 1:].flatten())
        return loss + aux

    def num_params(self, embedding=True):
        n = sum(p.numel() for p in self.parameters())
        return n if embedding else n - self.embed.weight.numel()

    def moe_layers(self):
        return [b.ffn for b in self.blocks if isinstance(b.ffn, MoE)]

    def active_params(self):
        """Parameters one token actually uses: MoE layers count only top_k of their routed experts."""
        unused = sum((m.n_experts - m.top_k) * m.expert_params for m in self.moe_layers())
        return self.num_params() - unused

    def flops_per_token(self, seq_len):
        """Training FLOPs per token: 6 per active weight (fwd + bwd, incl. the tied output head and MTP)
        plus attention scores, 12·layers·d·T (PaLM appendix B). A Gated DeltaNet layer has no T term; its
        state updates cost about 18·d·head_dim and the intra-chunk products about 12·d·chunk (estimate)."""
        d, layers = self.cfg.d_model, self.cfg.layers
        n_attn = sum(k["mixer"] == "attention" for k in layers) + (self.mtp is not None)
        n_gdn = sum(k["mixer"] == "gdn" for k in layers)
        chunk = (GDN_DEFAULTS | (self.cfg.gdn or {}))["chunk_size"]
        gdn = n_gdn * (18 * d * self.cfg.head_dim + 12 * d * chunk)
        return 6 * self.active_params() + 12 * n_attn * d * seq_len + gdn

    @torch.no_grad()
    def generate(self, idx, max_new_tokens, temperature=0.8, top_k=50):
        """Sampling without a KV cache: every step re-runs the full context (fine at lab scale)."""
        for _ in range(max_new_tokens):
            logits = self(idx[:, -self.cfg.max_seq_len:])[:, -1].float() / temperature
            if top_k:
                kth = torch.topk(logits, top_k).values[:, -1:]
                logits = logits.masked_fill(logits < kth, float("-inf"))
            idx = torch.cat([idx, torch.multinomial(F.softmax(logits, -1), 1)], dim=1)
        return idx
