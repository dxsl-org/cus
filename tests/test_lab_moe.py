import pytest

torch = pytest.importorskip("torch")

from lab.config import ModelConfig  # noqa: E402
from lab.layers.mlp import SwiGLU  # noqa: E402
from lab.layers.moe import MoE  # noqa: E402
from lab.model import LanguageModel  # noqa: E402


def cfg(moe=None, **kw):
    base = dict(vocab_size=97, d_model=32, n_layers=2, n_heads=4, n_kv_heads=2, ffn_hidden=64, max_seq_len=32)
    return ModelConfig(**(base | kw), moe=moe)


def test_single_expert_equals_dense_ffn():
    c = cfg({"n_experts": 1, "top_k": 1, "expert_hidden": 64})
    moe, dense = MoE(c), SwiGLU(c)
    with torch.no_grad():
        dense.w1.weight.copy_(moe.w1[0].T)
        dense.w3.weight.copy_(moe.w3[0].T)
        dense.w2.weight.copy_(moe.w2[0].T)
    x = torch.randn(2, 5, 32)
    assert torch.allclose(moe(x)[0], dense(x), atol=1e-5)


@pytest.mark.skipif(not torch.cuda.device_count(), reason="torch._grouped_mm needs CUDA")
def test_grouped_dispatch_matches_the_per_expert_loop_with_empty_experts():
    """Same routing and outputs, within bf16 rounding, including experts that receive no token."""
    c = cfg({"n_experts": 8, "top_k": 2, "expert_hidden": 48, "balance": "bias"}, d_model=64)
    loop = MoE(c).cuda()
    grouped = MoE(c).cuda()
    grouped.load_state_dict(loop.state_dict())
    loop.dispatch, grouped.dispatch = "loop", "grouped"
    with torch.no_grad():  # steer all tokens to experts 0-2 so five experts get an empty group
        for m in (loop, grouped):
            m.bias[:] = -100.0
            m.bias[:3] = 100.0
    x = torch.randn(3, 40, 64, device="cuda")
    outs = []
    for m in (loop, grouped):
        xi = x.clone().requires_grad_()
        with torch.autocast("cuda", dtype=torch.bfloat16):
            y, _ = m(xi)
        y.float().square().mean().backward()
        outs.append((y.float(), xi.grad, m.w1.grad))
    for a, b in zip(*outs):
        assert ((a - b).norm() / b.norm()).item() < 1e-2


@pytest.mark.parametrize("balance", ["aux", "bias"])
def test_router_receives_gradient(balance):
    moe = MoE(cfg({"n_experts": 4, "top_k": 2, "expert_hidden": 16, "balance": balance}))
    y, aux = moe(torch.randn(3, 7, 32))
    (y.pow(2).sum() + aux).backward()
    assert moe.router.weight.grad.abs().sum() > 0


def test_bias_balancing_spreads_a_collapsed_router():
    moe = MoE(cfg({"n_experts": 8, "top_k": 2, "expert_hidden": 16, "balance": "bias", "bias_rate": 0.01}))
    with torch.no_grad():
        moe.router.weight.zero_()  # equal scores: top-k would always pick the same two experts
    x = torch.randn(4, 16, 32)
    total = torch.zeros(8)
    for step in range(60):
        moe(x)
        if step >= 40:
            total += moe.load
        moe.balance_step()
    assert total.max() / total.mean() < 1.5


@pytest.mark.parametrize("moe", [{"n_experts": 4, "top_k": 2, "expert_hidden": 32},
                                 {"n_experts": 4, "top_k": 1, "n_shared": 1, "expert_hidden": 32}])
def test_active_params_match_the_dense_model_they_replace(moe):
    """Equal-FLOPs comparisons rely on this: k routed + shared experts of width 32 = one dense FFN of 64,
    so the MoE model uses exactly the dense model's parameters plus its routers."""
    dense, sparse = LanguageModel(cfg()), LanguageModel(cfg(moe))
    routers = sum(m.router.weight.numel() for m in sparse.moe_layers())
    assert sparse.active_params() == dense.num_params() + routers
    assert sparse.num_params() > sparse.active_params()


def test_moe_model_is_causal_and_keeps_first_layers_dense():
    torch.manual_seed(0)
    model = LanguageModel(cfg({"n_experts": 4, "top_k": 2, "expert_hidden": 16, "dense_first_layers": 1})).eval()
    assert [k["ffn"] for k in model.cfg.layers] == ["dense", "moe"]
    idx = torch.randint(0, 97, (2, 16))
    changed = idx.clone()
    changed[:, 10:] = (changed[:, 10:] + 1) % 97
    assert torch.allclose(model(idx)[:, :10], model(changed)[:, :10], atol=1e-5)
