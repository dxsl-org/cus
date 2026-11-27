"""
Random training windows drawn from per-source token shards according to a mix.

Windows are a pure function of (seed, step, micro-step), so a resumed run sees exactly the batches it
would have seen without the interruption, with no loader state in the checkpoint.
"""
import json
import zlib

import numpy as np

from lab import corpus


def resolve_mix(tokens_dir, mix, split="train", min_tokens=1):
    """{source_id: probability}. A mix key is a source_id or a domain; a domain's weight is spread over its
    sources in proportion to their token counts. Sources with fewer than min_tokens are dropped."""
    domains = corpus.domain_map()
    counts = {p.stem: json.loads(p.read_text(encoding="utf-8"))[f"{split}_tokens"]
              for p in tokens_dir.glob("*.json") if p.name != "manifest.json"}
    counts = {s: n for s, n in counts.items() if n >= min_tokens}
    weights = {}
    for key, w in mix.items():
        members = [key] if key in counts else [s for s, d in domains.items() if d == key and counts.get(s)]
        if not members:
            raise ValueError(f"mix key {key!r} is neither a tokenized source nor a domain with data")
        size = sum(counts[s] for s in members)
        for s in members:
            if s in weights:
                raise ValueError(f"source {s} is covered by two mix keys")
            weights[s] = w * counts[s] / size
    total = sum(weights.values())
    return {s: w / total for s, w in weights.items()}


class WindowSampler:
    def __init__(self, tokens_dir, mix, seq_len, split="train", seed=0):
        self.probs = resolve_mix(tokens_dir, mix, split, min_tokens=seq_len + 1)
        self.sources = list(self.probs)
        self.p = np.array([self.probs[s] for s in self.sources])
        self.data = {s: np.memmap(tokens_dir / f"{s}.{split}.bin", dtype=np.uint16, mode="r")
                     for s in self.sources}
        self.seq_len, self.seed = seq_len, seed

    def batch(self, n, *key):
        """(n, seq_len + 1) int64 windows for the given key, e.g. (step, micro_step)."""
        rng = np.random.default_rng([self.seed, *key])
        picks = rng.choice(len(self.sources), size=n, p=self.p)
        out = np.empty((n, self.seq_len + 1), dtype=np.int64)
        for row, src in enumerate(picks):
            data = self.data[self.sources[src]]
            start = rng.integers(0, len(data) - self.seq_len - 1)
            out[row] = data[start:start + self.seq_len + 1]
        return out


def fixed_windows(tokens_dir, mix, seq_len, n, split="heldout"):
    """Deterministic evaluation windows: identical across runs, models and resumes."""
    tag = zlib.crc32(json.dumps(mix, sort_keys=True).encode())
    return WindowSampler(tokens_dir, mix, seq_len, split, seed=tag).batch(n, 0)
