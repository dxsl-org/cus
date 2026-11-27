import numpy as np

from lab.compare_bpb import compare


def fake_losses(rng, domains=("law", "web"), n=64):
    """{domain: (nll, nbytes)} with window-to-window variation, as DomainEval.per_window returns."""
    out = {}
    for d in domains:
        nbytes = rng.integers(3000, 6000, n).astype(np.float64)
        out[d] = (nbytes * rng.uniform(0.4, 0.8, n), nbytes)
    return out


def test_uniform_improvement_has_tight_interval_around_it():
    a = fake_losses(np.random.default_rng(0))
    b = {d: (nll * 0.99, nbytes) for d, (nll, nbytes) in a.items()}
    res = compare(a, b, bootstrap=2000)
    for key in ("law", "web", "all"):
        assert abs(res[key]["diff_pct"] + 1.0) < 1e-6
        lo, hi = res[key]["ci95"]
        assert -1.001 < lo <= hi < -0.999
        assert res[key]["p_not_better"] == 0.0


def test_noisy_difference_has_an_interval_that_resamples_windows():
    rng = np.random.default_rng(1)
    a = fake_losses(rng)
    b = {d: (nll * rng.normal(1.0, 0.02, len(nll)), nbytes) for d, (nll, nbytes) in a.items()}
    res = compare(a, b, bootstrap=2000)
    lo, hi = res["all"]["ci95"]
    assert lo < res["all"]["diff_pct"] < hi and hi - lo > 0.05
    total = lambda s: sum(nll.sum() for nll, _ in s.values()) / sum(nb.sum() for _, nb in s.values()) / np.log(2)
    assert abs(res["all"]["a"] - round(total(a), 4)) < 1e-9


def test_different_windows_are_refused():
    rng = np.random.default_rng(2)
    a, b = fake_losses(rng), fake_losses(rng)
    try:
        compare(a, b, bootstrap=10)
    except ValueError:
        return
    raise AssertionError("windows with different byte counts must not be compared")
