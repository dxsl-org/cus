import pytest

torch = pytest.importorskip("torch")
pytest.importorskip("tokenizers")

from lab import tokenizer_train  # noqa: E402
from lab.config import ModelConfig  # noqa: E402
from lab.layers.norm_rope import apply_rope, rope_tables  # noqa: E402
from lab.model import LanguageModel  # noqa: E402


def tiny(**kw):
    cfg = dict(vocab_size=97, d_model=64, n_layers=2, n_heads=4, n_kv_heads=2, max_seq_len=32)
    return ModelConfig(**(cfg | kw))


def test_future_tokens_do_not_change_past_logits():
    torch.manual_seed(0)
    model = LanguageModel(tiny()).eval()
    idx = torch.randint(0, 97, (2, 16))
    changed = idx.clone()
    changed[:, 10:] = (changed[:, 10:] + 1) % 97
    a, b = model(idx), model(changed)
    assert torch.allclose(a[:, :10], b[:, :10], atol=1e-5)
    assert not torch.allclose(a[:, 10:], b[:, 10:], atol=1e-3)


def test_param_count_matches_formula():
    cfg = tiny()
    d, f, kv = cfg.d_model, cfg.ffn_hidden, cfg.n_kv_heads * cfg.head_dim
    per_layer = 2 * d * d + 2 * d * kv + 3 * d * f + 2 * d  # q,o + k,v + SwiGLU + two norms
    expected = cfg.vocab_size * d + cfg.n_layers * per_layer + d  # tied embedding + final norm
    assert LanguageModel(cfg).num_params() == expected


def test_rope_score_depends_only_on_distance():
    torch.manual_seed(0)
    cos, sin = rope_tables(16, 64, 10000.0)
    q, k = torch.randn(16), torch.randn(16)

    def score(m, n):
        place = lambda v, pos: apply_rope(v.expand(1, 1, pos + 1, 16).clone(), cos, sin)[0, 0, pos]  # noqa: E731
        return place(q, m) @ place(k, n)

    assert torch.allclose(score(5, 2), score(40, 37), atol=1e-4)
    assert not torch.allclose(score(5, 2), score(5, 4), atol=1e-3)


def test_token_bytes_sum_to_utf8_length():
    """bpb divides by these lengths, so they must add up to the exact UTF-8 size of any text."""
    texts = ["Điều 5. Người Việt Nam yêu hoà bình, năm 2024 có 12345 người.", "Chào bạn!\n\tcode()"] * 30
    tok = tokenizer_train.train(iter(texts), vocab_size=400)
    lengths = tokenizer_train.token_byte_lengths(tok)
    for text in texts[:2] + ["emoji 😀 và chữ lạ ẞ ǅ"]:
        assert sum(lengths[i] for i in tok.encode(text).ids) == len(text.encode())
    assert lengths[tok.token_to_id("<|endoftext|>")] == 0
