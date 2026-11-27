import torch.nn.functional as F
from torch import nn


class SwiGLU(nn.Module):
    """FFN(x) = W2(silu(W1 x) ⊙ W3 x). The gate W1 lets each hidden unit decide how much of W3 x passes."""

    def __init__(self, cfg, hidden=None):
        super().__init__()
        hidden = hidden or cfg.ffn_hidden
        self.w1 = nn.Linear(cfg.d_model, hidden, bias=False)
        self.w3 = nn.Linear(cfg.d_model, hidden, bias=False)
        self.w2 = nn.Linear(hidden, cfg.d_model, bias=False)
        self.w2.residual_out = True

    def forward(self, x):
        return self.w2(F.silu(self.w1(x)) * self.w3(x))
