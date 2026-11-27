"""Run configuration: one YAML file per run with `model:` and `train:` sections (lab/configs/*.yaml)."""
import dataclasses
import math
import pathlib

import yaml


@dataclasses.dataclass
class ModelConfig:
    vocab_size: int = 32768
    d_model: int = 512
    n_layers: int = 8
    n_heads: int = 8
    n_kv_heads: int = 4
    ffn_hidden: int | None = None  # default 8/3·d rounded up to 128: same params as a 4·d GELU FFN
    max_seq_len: int = 2048
    rope_theta: float = 10000.0
    norm_eps: float = 1e-5
    # Per-layer block types, e.g. [{mixer: attention, ffn: dense}, ...]. Default from the sections below:
    # mixer attention, or Gated DeltaNet with every gdn.attention_every-th layer attention (0 = no attention);
    # FFN dense, or MoE after the first moe.dense_first_layers layers.
    layers: list | None = None
    moe: dict | None = None  # n_experts, top_k, n_shared, expert_hidden, gate, balance, ... (moe.DEFAULTS)
    gdn: dict | None = None  # attention_every, conv_size, chunk_size, backend (gdn.DEFAULTS)
    attn_gate: bool = False  # sigmoid output gate on attention layers (layers/attention.py)
    mtp: dict | None = None  # multi-token prediction: {weight} (layers/mtp.py)
    # μP: width the hyperparameters were tuned at. Logits are scaled by base/d_model, hidden-matrix init std by
    # sqrt(base/d_model), and train.make_optimizer scales AdamW lrs; at d_model == base the model is unchanged.
    mup_base_width: int | None = None

    def __post_init__(self):
        if self.d_model % self.n_heads or self.n_heads % self.n_kv_heads:
            raise ValueError("d_model must divide by n_heads, n_heads by n_kv_heads")
        if self.ffn_hidden is None:
            self.ffn_hidden = -(-8 * self.d_model // 3 // 128) * 128
        if self.layers is None:
            n_dense = self.moe.get("dense_first_layers", 0) if self.moe else self.n_layers
            every = self.gdn.get("attention_every", 4) if self.gdn is not None else 1
            self.layers = [{"mixer": "attention" if every and (i + 1) % every == 0 else "gdn",
                            "ffn": "dense" if i < n_dense else "moe"} for i in range(self.n_layers)]
        if len(self.layers) != self.n_layers:
            raise ValueError(f"layers lists {len(self.layers)} blocks, n_layers is {self.n_layers}")

    @property
    def head_dim(self):
        return self.d_model // self.n_heads

    @property
    def init_scale(self):
        """μP factor on the init std of hidden matrices: sqrt(base / d_model), 1 without μP."""
        return math.sqrt(self.mup_base_width / self.d_model) if self.mup_base_width else 1.0


@dataclasses.dataclass
class TrainConfig:
    name: str
    mix: dict  # source_id or domain -> weight; a domain spreads its weight over its sources by size
    total_tokens: float
    batch_tokens: int = 262144  # tokens per optimizer step
    micro_batch: int = 16  # sequences per forward pass; batch_tokens / (micro_batch·seq_len) = accumulation
    seq_len: int = 2048
    lr: float = 3e-3
    min_lr_ratio: float = 0.1
    warmup_steps: int = 200
    weight_decay: float = 0.1
    betas: tuple = (0.9, 0.95)
    grad_clip: float = 1.0
    eval_every: int = 250
    eval_windows: int = 64  # held-out windows per domain
    ckpt_minutes: float = 30
    compile: bool = True
    seed: int = 0
    init_from: str | None = None  # run name whose final weights start this run (train.load_base_weights)
    init_ckpt: str = "ckpt.pt"  # file in the init_from run: ckpt-stable.pt starts from a WSD run's pre-decay weights
    optimizer: str = "adamw"  # adamw | muon (Muon for hidden matrices at muon_lr, AdamW at lr for the rest)
    muon_lr: float = 0.02
    schedule: str = "cosine"  # cosine | wsd (train.lr_factor)
    decay_fraction: float = 0.2  # wsd: share of steps in the final decay
    anneal_mix: dict | None = None  # wsd: mix used during the decay (annealing on high-quality data)

    def __post_init__(self):
        if self.optimizer not in ("adamw", "muon") or self.schedule not in ("cosine", "wsd"):
            raise ValueError(f"unknown optimizer {self.optimizer!r} or schedule {self.schedule!r}")
        if self.anneal_mix and self.schedule != "wsd":
            raise ValueError("anneal_mix needs schedule: wsd (it applies during the decay)")

    @property
    def grad_accum(self):
        per_micro = self.micro_batch * self.seq_len
        if self.batch_tokens % per_micro:
            raise ValueError("batch_tokens must be a multiple of micro_batch·seq_len")
        return self.batch_tokens // per_micro

    @property
    def total_steps(self):
        return int(self.total_tokens) // self.batch_tokens

    @property
    def decay_start(self):
        """First step of the wsd decay (and of anneal_mix)."""
        return self.total_steps - round(self.decay_fraction * self.total_steps)


def load_run(path):
    raw = yaml.safe_load(pathlib.Path(path).read_text(encoding="utf-8"))
    train = dict(raw["train"])
    train["total_tokens"] = float(train["total_tokens"])
    for key in ("lr", "weight_decay", "min_lr_ratio", "muon_lr", "decay_fraction"):
        if key in train:
            train[key] = float(train[key])
    return ModelConfig(**raw["model"]), TrainConfig(**train), raw
