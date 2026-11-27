"""
Muon for hidden weight matrices, including stacked expert weights (E, in, out) that torch.optim.Muon (2-D only)
rejects. Same update as torch.optim.Muon with adjust_lr_fn="original": Nesterov momentum, Newton–Schulz
orthogonalization (5 quintic steps in bf16), decoupled weight decay, step scaled by sqrt(max(1, fan_out/fan_in)).
That scale keeps the update's spectral norm independent of width, which is μP for these matrices: the same lr
works at every width.

Each parameter group says how its matrices are laid out: `layout="out_in"` (nn.Linear weight, (out, in)) or
`"in_out"` (MoE experts, (..., in, out)). Leading dims are a batch of independent matrices.
"""
import math

import torch

NS_COEFFS = (3.4445, -4.7750, 2.0315)


def orthogonalize(g, steps=5, eps=1e-7):
    """Approximate U·Vᵀ of g's SVD for every matrix in the batch (…, m, n), via quintic Newton–Schulz; the
    same fused bf16 steps as torch.optim.Muon (addmm), batched with baddbmm."""
    a, b, c = NS_COEFFS
    x = g.bfloat16().reshape(-1, *g.shape[-2:])
    tall = x.size(-2) > x.size(-1)
    if tall:
        x = x.mT
    x = x / x.norm(dim=(-2, -1), keepdim=True).clamp(min=eps)
    for _ in range(steps):
        s = x @ x.mT
        x = torch.baddbmm(x, torch.baddbmm(s, s, s, beta=b, alpha=c), x, beta=a)
    return (x.mT if tall else x).reshape(g.shape).to(g.dtype)


class Muon(torch.optim.Optimizer):
    def __init__(self, params, lr=0.02, weight_decay=0.1, momentum=0.95, nesterov=True, ns_steps=5,
                 layout="out_in"):
        super().__init__(params, dict(lr=lr, weight_decay=weight_decay, momentum=momentum, nesterov=nesterov,
                                      ns_steps=ns_steps, layout=layout))

    @torch.no_grad()
    def step(self):
        for group in self.param_groups:
            for p in group["params"]:
                if p.grad is None:
                    continue
                buf = self.state[p].setdefault("momentum", torch.zeros_like(p))
                buf.lerp_(p.grad, 1 - group["momentum"])
                update = p.grad.lerp(buf, group["momentum"]) if group["nesterov"] else buf
                fan_out, fan_in = (p.size(-2), p.size(-1)) if group["layout"] == "out_in" else (p.size(-1), p.size(-2))
                scale = math.sqrt(max(1.0, fan_out / fan_in))
                p.mul_(1 - group["lr"] * group["weight_decay"])
                p.add_(orthogonalize(update, group["ns_steps"]), alpha=-group["lr"] * scale)
