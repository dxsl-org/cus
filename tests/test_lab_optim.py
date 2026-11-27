import math

import pytest

torch = pytest.importorskip("torch")

from lab.config import ModelConfig, TrainConfig  # noqa: E402
from lab.model import LanguageModel  # noqa: E402
from lab.optim_muon import orthogonalize  # noqa: E402
from lab.train import lr_factor, make_optimizer  # noqa: E402


def tcfg(**kw):
    return TrainConfig(**({"name": "t", "mix": {"x": 1.0}, "total_tokens": 1000 * 262144, "warmup_steps": 10} | kw))


def mcfg(**kw):
    base = dict(vocab_size=97, d_model=64, n_layers=2, n_heads=4, n_kv_heads=2, max_seq_len=32)
    return ModelConfig(**(base | kw))


def test_default_schedule_is_the_original_cosine():
    cfg = tcfg()
    assert lr_factor(0, cfg) == pytest.approx(0.1)
    assert lr_factor(10, cfg) == pytest.approx(1.0)
    assert lr_factor(cfg.total_steps, cfg) == pytest.approx(cfg.min_lr_ratio)
    mid = 10 + (cfg.total_steps - 10) // 2
    assert lr_factor(mid, cfg) == pytest.approx(cfg.min_lr_ratio + (1 - cfg.min_lr_ratio) / 2, abs=1e-3)


def test_wsd_is_flat_until_the_decay_then_falls_to_the_floor():
    cfg = tcfg(schedule="wsd", decay_fraction=0.2)
    assert cfg.decay_start == 800
    assert lr_factor(799, cfg) == 1.0
    tail = [lr_factor(s, cfg) for s in range(800, 1001)]
    assert all(a >= b for a, b in zip(tail, tail[1:]))
    assert tail[-1] == pytest.approx(cfg.min_lr_ratio)


def test_anneal_mix_requires_wsd():
    with pytest.raises(ValueError, match="wsd"):
        tcfg(anneal_mix={"law": 1.0})


@pytest.mark.parametrize("shape", [(48, 32), (32, 48)])
def test_newton_schulz_matches_torch_muon(shape):
    from torch.optim._muon import _zeropower_via_newtonschulz as reference
    g = torch.randn(*shape)
    expected = reference(g, (3.4445, -4.7750, 2.0315), 5, 1e-7).float()
    assert ((orthogonalize(g).float() - expected).norm() / expected.norm()).item() < 1e-2  # bf16 rounding only


def test_newton_schulz_treats_leading_dims_as_independent_matrices():
    g = torch.randn(3, 24, 40)
    batched = orthogonalize(g)
    assert all(torch.allclose(batched[i], orthogonalize(g[i])) for i in range(3))
    s = torch.linalg.svdvals(batched.float())
    assert s.min() > 0.5 and s.max() < 1.3  # quintic NS: singular values pushed into ~[0.7, 1.2]


def test_default_adamw_keeps_two_groups_for_old_checkpoints():
    opt = make_optimizer(LanguageModel(mcfg()), tcfg())
    assert len(opt.param_groups) == 2 and all(g["base_lr"] == 3e-3 for g in opt.param_groups)


def test_muon_mup_groups_cover_every_parameter_once():
    model = LanguageModel(mcfg(d_model=128, mup_base_width=64, gdn={"attention_every": 2},
                               moe={"n_experts": 4, "top_k": 2, "expert_hidden": 32}))
    opt = make_optimizer(model, tcfg(optimizer="muon", lr=1e-3, muon_lr=0.02))
    seen = [id(p) for g in opt.param_groups for p in g["params"]]
    assert sorted(seen) == sorted(id(p) for p in model.parameters())
    groups = {id(p): g for g in opt.param_groups for p in g["params"]}
    moe = next(m for m in model.modules() if hasattr(m, "router"))
    assert groups[id(moe.w1)]["layout"] == "in_out" and groups[id(moe.w1)]["base_lr"] == 0.02
    assert groups[id(moe.router.weight)]["base_lr"] == pytest.approx(1e-3 / 2)  # readout: lr ÷ width multiplier
    assert groups[id(model.embed.weight)]["base_lr"] == 1e-3


def test_mup_at_the_base_width_is_the_unchanged_model():
    torch.manual_seed(0)
    plain = LanguageModel(mcfg())
    torch.manual_seed(0)
    mup = LanguageModel(mcfg(mup_base_width=64))
    x = torch.randint(0, 97, (2, 16))
    assert torch.equal(plain(x), mup(x))


def test_muon_training_reduces_the_loss():
    torch.manual_seed(0)
    model = LanguageModel(mcfg())
    opt = make_optimizer(model, tcfg(optimizer="muon", lr=3e-3, muon_lr=0.02))
    x = torch.randint(0, 97, (4, 17))
    losses = []
    for _ in range(30):
        loss = model(x[:, :-1], x[:, 1:])
        loss.backward()
        opt.step()
        opt.zero_grad()
        losses.append(loss.item())
    assert losses[-1] < 0.5 * losses[0]
    state = opt.state_dict()  # resumable: the combined optimizer round-trips its state
    opt.load_state_dict(state)
    assert math.isfinite(losses[-1])
