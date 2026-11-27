import torch
from torch import nn


class RMSNorm(nn.Module):
    """x / rms(x) · g. No mean subtraction and no bias, unlike LayerNorm; computed in fp32 for stability."""

    def __init__(self, dim, eps=1e-5):
        super().__init__()
        self.eps = eps
        self.weight = nn.Parameter(torch.ones(dim))

    def forward(self, x):
        xf = x.float()
        xf = xf * torch.rsqrt(xf.pow(2).mean(-1, keepdim=True) + self.eps)
        return (xf * self.weight).type_as(x)


def rope_tables(head_dim, max_len, theta, device=None):
    """cos/sin of shape (max_len, head_dim/2). Pair i of a head rotates at frequency theta^(-2i/head_dim)."""
    inv_freq = theta ** (-torch.arange(0, head_dim, 2, device=device, dtype=torch.float32) / head_dim)
    angles = torch.outer(torch.arange(max_len, device=device, dtype=torch.float32), inv_freq)
    return angles.cos(), angles.sin()


def apply_rope(x, cos, sin):
    """Rotate (B, H, T, D) by position. The two halves of the head dim form the (real, imag) pairs, so
    q·k after rotation depends only on the distance between the two positions."""
    t = x.shape[-2]
    cos, sin = cos[:t].to(x.dtype), sin[:t].to(x.dtype)
    x1, x2 = x.chunk(2, dim=-1)
    return torch.cat([x1 * cos - x2 * sin, x1 * sin + x2 * cos], dim=-1)
