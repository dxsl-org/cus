import torch
import torch.nn.functional as F
from torch import nn

from lab.layers.norm_rope import apply_rope


class Attention(nn.Module):
    """Causal self-attention with grouped-query heads: n_heads query heads share n_kv_heads key/value
    heads, shrinking the KV cache by n_heads/n_kv_heads at almost no quality cost. With cfg.attn_gate the
    output is multiplied elementwise by sigmoid(W_g x) (gated attention, as in Qwen3-Next): a head can switch
    itself off for a token instead of dumping attention on the first token ("attention sink")."""

    def __init__(self, cfg):
        super().__init__()
        self.n_heads, self.n_kv, self.head_dim = cfg.n_heads, cfg.n_kv_heads, cfg.head_dim
        self.wq = nn.Linear(cfg.d_model, cfg.n_heads * cfg.head_dim, bias=False)
        self.wk = nn.Linear(cfg.d_model, cfg.n_kv_heads * cfg.head_dim, bias=False)
        self.wv = nn.Linear(cfg.d_model, cfg.n_kv_heads * cfg.head_dim, bias=False)
        self.wo = nn.Linear(cfg.n_heads * cfg.head_dim, cfg.d_model, bias=False)
        self.wo.residual_out = True
        self.wg = nn.Linear(cfg.d_model, cfg.n_heads * cfg.head_dim, bias=False) if cfg.attn_gate else None

    def forward(self, x, rope):
        b, t, _ = x.shape
        q = self.wq(x).view(b, t, self.n_heads, self.head_dim).transpose(1, 2)
        k = self.wk(x).view(b, t, self.n_kv, self.head_dim).transpose(1, 2)
        v = self.wv(x).view(b, t, self.n_kv, self.head_dim).transpose(1, 2)
        q, k = apply_rope(q, *rope), apply_rope(k, *rope)
        if self.n_kv != self.n_heads:
            rep = self.n_heads // self.n_kv
            k, v = k.repeat_interleave(rep, dim=1), v.repeat_interleave(rep, dim=1)
        y = F.scaled_dot_product_attention(q, k, v, is_causal=True).transpose(1, 2).reshape(b, t, -1)
        if self.wg is not None:
            y = y * torch.sigmoid(self.wg(x))
        return self.wo(y)
