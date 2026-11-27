import pytest

torch = pytest.importorskip("torch")

from lab.config import ModelConfig  # noqa: E402
from lab.layers.gdn import chunk_gated_delta_rule, recurrent_gated_delta_rule  # noqa: E402
from lab.model import LanguageModel  # noqa: E402


def gdn_inputs(b=2, t=150, h=3, dk=8, dv=6, dtype=torch.float64, device="cpu", seed=0):
    gen = torch.Generator().manual_seed(seed)
    rnd = lambda *s: torch.randn(*s, generator=gen, dtype=dtype)  # noqa: E731
    q = torch.nn.functional.normalize(rnd(b, t, h, dk), dim=-1)
    k = torch.nn.functional.normalize(rnd(b, t, h, dk), dim=-1)
    v = rnd(b, t, h, dv)
    g = -torch.rand(b, t, h, generator=gen, dtype=dtype)  # decay exp(g) ∈ (e^-1, 1]
    beta = torch.rand(b, t, h, generator=gen, dtype=dtype)
    state = rnd(b, h, dk, dv)
    return [x.to(device).requires_grad_() for x in (q, k, v, g, beta, state)]


def test_chunk_form_matches_recurrence_values_and_gradients():
    """T=150 is not a multiple of the chunk, and a nonzero initial state is carried across 3 chunks."""
    a = gdn_inputs()
    b = [x.detach().clone().requires_grad_() for x in a]
    o_ref, s_ref = recurrent_gated_delta_rule(*a[:5], initial_state=a[5])
    o_chk, s_chk = chunk_gated_delta_rule(*b[:5], initial_state=b[5], chunk_size=64)
    assert torch.allclose(o_chk, o_ref, atol=1e-9) and torch.allclose(s_chk, s_ref, atol=1e-9)
    weights = torch.randn_like(o_ref)
    ((o_ref * weights).sum() + s_ref.sum()).backward()
    ((o_chk * weights).sum() + s_chk.sum()).backward()
    for x, y in zip(a, b):
        assert torch.allclose(y.grad, x.grad, atol=1e-8)


def test_hybrid_model_layout_and_causality():
    """Also catches a non-causal short convolution (padding on the wrong side leaks future tokens)."""
    cfg = ModelConfig(vocab_size=97, d_model=32, n_layers=4, n_heads=4, n_kv_heads=2, max_seq_len=96,
                      gdn={"attention_every": 2, "chunk_size": 16}, attn_gate=True)
    assert [k["mixer"] for k in cfg.layers] == ["gdn", "attention", "gdn", "attention"]
    torch.manual_seed(0)
    model = LanguageModel(cfg).eval()
    idx = torch.randint(0, 97, (2, 40))
    changed = idx.clone()
    changed[:, 25:] = (changed[:, 25:] + 1) % 97
    a, b = model(idx), model(changed)
    assert torch.allclose(a[:, :25], b[:, :25], atol=1e-5)
    assert not torch.allclose(a[:, 25:], b[:, 25:], atol=1e-3)


def test_mtp_adds_to_training_loss_only():
    cfg = ModelConfig(vocab_size=97, d_model=32, n_layers=2, n_heads=4, n_kv_heads=2, max_seq_len=32,
                      mtp={"weight": 0.3})
    torch.manual_seed(0)
    model = LanguageModel(cfg)
    idx = torch.randint(0, 97, (2, 17))
    x, y = idx[:, :-1], idx[:, 1:]
    train_loss = model.train()(x, y)
    eval_loss = model.eval()(x, y)
    assert train_loss > eval_loss  # MTP cross-entropy (≈ ln 97 · 0.3 at init) only in training
    assert torch.allclose(model(x, y, reduction="none").mean(), eval_loss, atol=1e-6)


@pytest.mark.skipif(not torch.cuda.device_count(), reason="fla kernels need CUDA")
def test_fla_kernel_matches_reference():
    pytest.importorskip("fla")
    from lab.layers.gdn import _fla_gated_delta_rule
    q, k, v, g, beta, _ = (x.detach() for x in gdn_inputs(t=200, h=4, dk=64, dv=64, dtype=torch.float32))
    o_ref, _ = recurrent_gated_delta_rule(q, k, v, g, beta)
    cuda = [x.cuda() for x in (q, k, v, g, beta)]
    o32 = _fla_gated_delta_rule(*cuda)[0].float().cpu()
    o16 = _fla_gated_delta_rule(*(x.bfloat16() if i < 3 else x for i, x in enumerate(cuda)))[0].float().cpu()
    assert (o32 - o_ref).abs().max() < 1e-3
    assert (o16 - o_ref).abs().max() < 5e-2  # bf16 inputs: ~3 significant digits
