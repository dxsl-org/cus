"""
Gated DeltaNet (Yang et al. 2024): linear attention whose memory is a fixed-size matrix per head.

Per head, with state S (K×V), decay a_t = exp(g_t) ∈ (0, 1] and write strength β_t ∈ (0, 1):
    S ← a_t · S                          forget a little of everything (the gate)
    S ← S + β_t · k_t (v_t − Sᵀk_t)ᵀ      delta rule: store only what S got wrong about k_t
    o_t = Sᵀ q_t · K^-½
Memory per head is K×V whatever the context length, so generation needs no growing KV cache; the cost is
lossy recall, which is why most models interleave a few full-attention layers.

Three implementations of the same recurrence, layout [B, T, H, D]:
  recurrent — the loop above, one token at a time: the readable reference.
  chunk     — the same in parallel chunks of C tokens (WY form, below), plain PyTorch.
  fla       — Triton kernels of flash-linear-attention (CUDA only), the one used for training.
"""
import math

import torch
import torch.nn.functional as F
from torch import nn

from lab.layers.norm_rope import RMSNorm

DEFAULTS = {"attention_every": 4, "conv_size": 4, "chunk_size": 64, "backend": "auto"}


def _upcast(x):
    """At least fp32 (fp64 stays fp64, so tests can compare the two forms exactly)."""
    return x.to(torch.promote_types(x.dtype, torch.float32))


def recurrent_gated_delta_rule(q, k, v, g, beta, scale=None, initial_state=None):
    """(o, final_state). q, k: [B,T,H,K]; v: [B,T,H,V]; g (log decay), beta: [B,T,H]; state [B,H,K,V]."""
    b, t, h, dk = q.shape
    scale = scale or dk ** -0.5
    q, k, v, g, beta = (_upcast(x) for x in (q, k, v, g, beta))
    s = q.new_zeros(b, h, dk, v.shape[-1]) if initial_state is None else _upcast(initial_state)
    out = []
    for i in range(t):
        s = s * g[:, i].exp()[..., None, None]
        error = v[:, i] - torch.einsum("bhkv,bhk->bhv", s, k[:, i])
        s = s + torch.einsum("bhk,bhv->bhkv", k[:, i], beta[:, i, :, None] * error)
        out.append(torch.einsum("bhkv,bhk->bhv", s, q[:, i] * scale))
    return torch.stack(out, dim=1), s


def chunk_gated_delta_rule(q, k, v, g, beta, scale=None, initial_state=None, chunk_size=64):
    """Same result as recurrent_gated_delta_rule, C tokens at a time.

    Inside a chunk, with G_i = Σ_{j≤i} g_j and S the state at chunk start, the corrections u_i = β_i·(v_i − …)
    written at each step satisfy a unit lower-triangular system (I + A)·U = βV − (β·e^G·K)·S with
    A_ij = β_i·e^{G_i−G_j}·(k_i·k_j) for j < i. One triangular solve replaces C sequential steps; then
        O     = (e^G·Q)·S + (e^{G_i−G_j}·QKᵀ, j ≤ i)·U
        S_new = e^{G_C}·S + (e^{G_C−G}·K)ᵀ·U
    """
    b, t, h, dk = q.shape
    dv, c = v.shape[-1], chunk_size
    scale = scale or dk ** -0.5
    pad = -t % c  # padded steps have g = 0 (no decay) and β = 0 (no write): the state passes through
    q, k, v = (F.pad(_upcast(x), (0, 0, 0, 0, 0, pad)) for x in (q, k, v))
    g, beta = (F.pad(_upcast(x), (0, 0, 0, pad)) for x in (g, beta))
    n = (t + pad) // c
    # [B, H, N, C, D]
    q, k, v = (x.view(b, n, c, h, -1).permute(0, 3, 1, 2, 4) for x in (q, k, v))
    g, beta = (x.view(b, n, c, h).permute(0, 3, 1, 2) for x in (g, beta))
    q = q * scale
    cum = g.cumsum(-1)
    eye = torch.eye(c, dtype=q.dtype, device=q.device)
    strict = torch.ones(c, c, dtype=torch.bool, device=q.device).triu(0)  # masks j ≥ i
    incl = strict.triu(1)  # masks j > i
    s = q.new_zeros(b, h, dk, dv) if initial_state is None else _upcast(initial_state)
    out = []
    for i in range(n):
        qi, ki, vi, gi, bi = q[:, :, i], k[:, :, i], v[:, :, i], cum[:, :, i], beta[:, :, i]
        diff = gi[..., :, None] - gi[..., None, :]
        a = bi[..., None] * diff.masked_fill(strict, -math.inf).exp() * (ki @ ki.transpose(-1, -2))
        rhs = torch.cat([bi[..., None] * vi, (bi * gi.exp())[..., None] * ki], dim=-1)
        uv, w = torch.linalg.solve_triangular(eye + a, rhs, upper=False, unitriangular=True).split([dv, dk], -1)
        u = uv - w @ s
        scores = (qi @ ki.transpose(-1, -2)) * diff.masked_fill(incl, -math.inf).exp()
        out.append((qi * gi.exp()[..., None]) @ s + scores @ u)
        s = gi[..., -1, None, None].exp() * s + (ki * (gi[..., -1:] - gi).exp()[..., None]).transpose(-1, -2) @ u
    o = torch.stack(out, dim=2).flatten(2, 3)[:, :, :t]  # [B, H, T, V]
    return o.transpose(1, 2), s


@torch.compiler.disable
def _fla_gated_delta_rule(q, k, v, g, beta):
    """fla needs q, k, v, beta in one dtype (it builds the WY matrix in k's dtype and multiplies it with v);
    under autocast the L2 norm returns fp32 q, k while v is bf16, so everything follows v. g stays fp32."""
    from fla.ops.gated_delta_rule import chunk_gated_delta_rule as fla_chunk
    dt = v.dtype
    return fla_chunk(q.to(dt), k.to(dt), v, g.float(), beta.to(dt))


class GatedDeltaNet(nn.Module):
    """Token mixer with the same interface as Attention (the RoPE argument is unused: the conv and the
    decaying state carry position). Follows the fla / Qwen3-Next layer: short causal conv + SiLU on q, k, v;
    L2-normalized q, k; β = sigmoid(W_b x); g = −exp(A_log)·softplus(W_a x + dt_bias) per head; output
    RMSNorm per head gated by silu(W_g x)."""

    def __init__(self, cfg):
        super().__init__()
        m = DEFAULTS | (cfg.gdn or {})
        self.backend, self.chunk_size = m["backend"], m["chunk_size"]
        self.n_heads, self.head_dim, d = cfg.n_heads, cfg.head_dim, cfg.d_model
        self.wqkv = nn.Linear(d, 3 * d, bias=False)
        self.conv = nn.Conv1d(3 * d, 3 * d, m["conv_size"], groups=3 * d, padding=m["conv_size"] - 1, bias=False)
        self.wa = nn.Linear(d, self.n_heads, bias=False)
        self.wb = nn.Linear(d, self.n_heads, bias=False)
        self.wg = nn.Linear(d, d, bias=False)
        self.wo = nn.Linear(d, d, bias=False)
        self.wo.residual_out = True
        self.o_norm = RMSNorm(self.head_dim, cfg.norm_eps)
        # Decay rates spread over heads: A ~ U(0, 16), softplus(dt_bias) ~ logU(1e-3, 0.1) (Mamba-2 init).
        self.A_log = nn.Parameter(torch.empty(self.n_heads).uniform_(1e-4, 16).log())
        dt = torch.exp(torch.rand(self.n_heads) * (math.log(0.1) - math.log(1e-3)) + math.log(1e-3))
        self.dt_bias = nn.Parameter(dt + torch.log(-torch.expm1(-dt)))  # inverse softplus

    def core(self, q, k, v, g, beta):
        backend = self.backend
        if backend == "auto":
            backend = "fla" if q.is_cuda else "chunk"
        if backend == "fla":
            return _fla_gated_delta_rule(q, k, v, g, beta)[0]
        if backend == "chunk":
            return chunk_gated_delta_rule(q, k, v, g, beta, chunk_size=self.chunk_size)[0].to(v.dtype)
        return recurrent_gated_delta_rule(q, k, v, g, beta)[0].to(v.dtype)

    def forward(self, x, rope=None):
        b, t, d = x.shape
        qkv = self.conv(self.wqkv(x).transpose(1, 2))[..., :t]  # causal: keep the first T outputs
        q, k, v = F.silu(qkv).transpose(1, 2).view(b, t, 3, self.n_heads, self.head_dim).unbind(2)
        q, k = F.normalize(q, dim=-1), F.normalize(k, dim=-1)
        beta = self.wb(x).float().sigmoid()
        g = -self.A_log.exp() * F.softplus(self.wa(x).float() + self.dt_bias)
        o = self.core(q, k, v, g, beta)
        o = self.o_norm(o) * F.silu(self.wg(x)).view(b, t, self.n_heads, self.head_dim)
        return self.wo(o.reshape(b, t, d))
