"""
Run a causal LM on an eval set and score it per group (and per lang / domain / difficulty within a group).

    uv run --group eval python -m eval.harness --model Qwen/Qwen2.5-0.5B-Instruct \
        --evalset evalset-v1 --mode loglik|cloze|generate [--batch-size 16] [--limit N]
    uv run --group eval --group lab python -m eval.harness --model lab:dense-m --evalset evalset-v2 --mode cloze
    ... --items path/to/items.jsonl      (unfrozen trial run; results are marked unfrozen)

loglik: MCQ only. Plain-text prompt ending in an answer cue, compare the log-probability of
  " A", " B", ...; needs a model that has learned the MCQ format (small base models sit at chance).
cloze: MCQ only. Score each choice's own text (meta.cloze sentence with the blank filled, else the stem +
  answer cue + choice); acc per byte, acc_raw on sums, and p_correct, the mass on the gold choice. Works
  from tiny base models up; compare two runs with `python -m eval.compare`.
generate: every kind, Hugging Face models only. Chat template when the tokenizer has one, greedy decoding,
  scored by the item's own verifier (code runs through the unit_test sandbox).

Reproducibility: fixed item order (kind, prompt length, id), fixed batch size, greedy decoding
and deterministic torch settings, so the same model + evalset + mode + batch size reproduces
the same per-item outputs. Results: DATA_ROOT/eval/runs/<evalset>/<model>/<mode>.{jsonl,json}.
"""
import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timezone

os.environ.setdefault("CUBLAS_WORKSPACE_CONFIG", ":4096:8")
import torch  # noqa: E402  (env must be set first)
from transformers import AutoModelForCausalLM, AutoTokenizer  # noqa: E402

from eval import scorers  # noqa: E402
from pipeline.paths import DATA_ROOT  # noqa: E402

# Generation budget by answer type; numeric items are solved step by step before "Đáp số:".
MAX_NEW_TOKENS = {"eval_mcq": 16, "eval_open": 64, "numeric": 512, "eval_code": 768}


def _budget_key(item):
    return "numeric" if scorers.is_numeric(item) else item["kind"]


def load_items(evalset=None, items_path=None):
    """Items of a frozen eval set (checked against its MANIFEST) or of a trial JSONL."""
    if items_path:
        with open(items_path, encoding="utf-8") as fh:
            return [json.loads(x) for x in fh], f"unfrozen-{os.path.basename(items_path)}"
    root = DATA_ROOT / "eval" / evalset
    text = (root / "items.jsonl").read_text(encoding="utf-8")
    manifest = json.loads((root / "MANIFEST.json").read_text(encoding="utf-8"))
    if hashlib.sha256(text.encode("utf-8")).hexdigest() != manifest["files"]["items.jsonl"]:
        raise SystemExit(f"{evalset}/items.jsonl does not match its MANIFEST; refusing to score")
    return [json.loads(x) for x in text.splitlines()], evalset


class HFModel:
    """A Hugging Face causal LM. No BOS is added: contexts are scored exactly as tokenized."""

    def __init__(self, name):
        self.tokenizer = AutoTokenizer.from_pretrained(name)
        self.tokenizer.padding_side = "left"
        if self.tokenizer.pad_token is None:
            self.tokenizer.pad_token = self.tokenizer.eos_token
        self.model = AutoModelForCausalLM.from_pretrained(name, dtype=torch.bfloat16, device_map="cuda").eval()
        self.prefix, self.pad_id = [], self.tokenizer.pad_token_id

    def encode(self, text):
        return self.tokenizer(text, add_special_tokens=False)["input_ids"]

    def logits(self, ids, mask):
        return self.model(input_ids=ids, attention_mask=mask).logits


class LabModel:
    """A lab checkpoint (`lab:<run>`, lab/ architecture + lab tokenizer). Every context starts with EOT, the
    document separator the model saw before each training document. Needs `--group lab` for its imports."""

    def __init__(self, run):
        from tokenizers import Tokenizer

        from lab import corpus
        from lab.evaluate import load_checkpoint
        self.model, _, _ = load_checkpoint(run, "cuda")
        self.model.eval()
        self.tok = Tokenizer.from_file(str(corpus.TOKENIZER_PATH))
        self.prefix, self.pad_id = [self.tok.token_to_id(corpus.EOT)], 0

    def encode(self, text):
        return self.tok.encode(text).ids

    def logits(self, ids, mask):  # causal and right-padded: padding never precedes a scored position
        with torch.autocast("cuda", dtype=torch.bfloat16):
            return self.model(ids)


def load_model(name):
    return LabModel(name.removeprefix("lab:")) if name.startswith("lab:") else HFModel(name)


def _batches(seq, size):
    for i in range(0, len(seq), size):
        yield seq[i:i + size]


@torch.no_grad()
def score_continuations(lm, pairs):
    """Summed log-probability of each continuation given its context, one padded batch."""
    seqs, spans = [], []
    for context, continuation in pairs:
        ctx_ids = lm.prefix + lm.encode(context)
        cont_ids = lm.encode(continuation)
        seqs.append(ctx_ids + cont_ids)
        spans.append((len(ctx_ids), len(ctx_ids) + len(cont_ids)))
    width = max(map(len, seqs))
    ids = torch.full((len(seqs), width), lm.pad_id)
    mask = torch.zeros_like(ids)
    for row, seq in enumerate(seqs):  # right padding keeps positions identical across choices
        ids[row, :len(seq)] = torch.tensor(seq)
        mask[row, :len(seq)] = 1
    logprobs = torch.log_softmax(lm.logits(ids.cuda(), mask.cuda()).float(), -1)
    return [sum(logprobs[row, pos - 1, seqs[row][pos]].item() for pos in range(a, b))
            for row, (a, b) in enumerate(spans)]


def _record(item):
    return {"id": item["id"], "group": item["group"], "lang": item["lang"], "domain": item.get("domain"),
            "difficulty": item.get("difficulty")}


def run_loglik(lm, items, batch_size):
    out = []
    for item in [i for i in items if i["kind"] == "eval_mcq"]:
        context = scorers.loglik_context(item)
        scores = score_continuations(lm, [(context, f" {label}") for label in scorers.labels(item)])
        pred = scorers.labels(item)[max(range(len(scores)), key=scores.__getitem__)]
        out.append(_record(item) | {"pred": pred, "scores": [round(s, 4) for s in scores],
                                    "correct": pred == item["answer_key"]})
    return out


def run_cloze(lm, items, batch_size):
    out = []
    for item in [i for i in items if i["kind"] == "eval_mcq"]:
        pairs = scorers.cloze_pairs(item)
        scores = score_continuations(lm, pairs)
        n_bytes = [len(continuation.encode("utf-8")) for _, continuation in pairs]
        out.append(_record(item) | scorers.cloze_result(item, scores, n_bytes))
    return out


def _chat(tokenizer, item):
    message = [{"role": "user", "content": scorers.generate_prompt(item)}]
    if tokenizer.chat_template:
        return tokenizer.apply_chat_template(message, tokenize=False, add_generation_prompt=True)
    return message[0]["content"] + "\n"


@torch.no_grad()
def run_generate(lm, items, batch_size):
    if not isinstance(lm, HFModel):
        raise SystemExit("generate mode needs a Hugging Face chat model")
    tokenizer, model = lm.tokenizer, lm.model
    prompts = {i["id"]: _chat(tokenizer, i) for i in items}
    ordered = sorted(items, key=lambda i: (_budget_key(i), len(prompts[i["id"]]), i["id"]))
    out = []
    for kind in sorted({_budget_key(i) for i in ordered}):
        for batch in _batches([i for i in ordered if _budget_key(i) == kind], batch_size):
            enc = tokenizer([prompts[i["id"]] for i in batch], return_tensors="pt", padding=True,
                            add_special_tokens=False).to("cuda")
            gen = model.generate(**enc, do_sample=False, max_new_tokens=MAX_NEW_TOKENS[kind],
                                 pad_token_id=tokenizer.pad_token_id, temperature=None, top_p=None, top_k=None)
            texts = tokenizer.batch_decode(gen[:, enc["input_ids"].shape[1]:], skip_special_tokens=True)
            for item, text in zip(batch, texts):
                correct, detail = scorers.score_generation(item, text)
                out.append(_record(item) | {"output": text, "correct": correct, "detail": detail[:200]})
            print(f"  {kind}: {len(out)}/{len(items)}", flush=True)
    return sorted(out, key=lambda r: r["id"])


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", required=True)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--evalset")
    source.add_argument("--items")
    parser.add_argument("--mode", choices=["loglik", "cloze", "generate"], required=True)
    parser.add_argument("--batch-size", type=int, default=16)
    parser.add_argument("--limit", type=int, default=0, help="first N items by id (smoke runs)")
    parser.add_argument("--seed", type=int, default=0)
    args = parser.parse_args()

    torch.manual_seed(args.seed)
    torch.use_deterministic_algorithms(True, warn_only=True)
    torch.backends.cudnn.benchmark = False
    items, evalset = load_items(args.evalset, args.items)
    items = sorted(items, key=lambda i: i["id"])[: args.limit or None]
    lm = load_model(args.model)
    runner = {"loglik": run_loglik, "cloze": run_cloze, "generate": run_generate}[args.mode]
    results = runner(lm, items, args.batch_size)
    summary = {"model": args.model, "evalset": evalset, "mode": args.mode, "batch_size": args.batch_size,
               "seed": args.seed, "limit": args.limit, "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
               "groups": scorers.summarize(results)}
    out_dir = DATA_ROOT / "eval" / "runs" / evalset / args.model.replace("/", "__").replace(":", "_")
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = f"{args.mode}" + (f"-limit{args.limit}" if args.limit else "")
    with open(out_dir / f"{stem}.jsonl", "w", encoding="utf-8", newline="\n") as fh:
        fh.writelines(json.dumps(r, ensure_ascii=False) + "\n" for r in results)
    (out_dir / f"{stem}.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    for key, s in summary["groups"].items():
        extra = f"  p_correct={s['p_correct']:.3f}" if "p_correct" in s else ""
        print(f"{key:24} n={s['n']:5}  acc={s['acc']:.3f}  ci95=[{s['ci95'][0]:.3f}, {s['ci95'][1]:.3f}]{extra}")


if __name__ == "__main__":
    main()
