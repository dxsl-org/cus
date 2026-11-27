"""
Temporary lab tokenizer: byte-level BPE, 32k vocab, Vietnamese only. Not the phase-10 tokenizer.

  uv run --group lab python -m lab.tokenizer_train train      # ~2 GB sample → TOKENIZER_PATH
  uv run --group lab python -m lab.tokenizer_train fertility  # tokens per syllable vs Qwen2.5, per domain

The sample skips held-out documents so the bpb sets never shaped the vocabulary.
"""
import argparse
import itertools
import json
import sys

from tokenizers import Regex, Tokenizer, decoders, models, pre_tokenizers, trainers

from lab import corpus

VOCAB_SIZE = 32768  # < 2**16: token ids fit uint16 shards
# Llama-3 style split: letters (incl. precomposed Vietnamese), digits in groups of ≤3, punctuation, spaces.
SPLIT = (r"(?i:'s|'t|'re|'ve|'m|'ll|'d)|[^\r\n\p{L}\p{N}]?\p{L}+|\p{N}{1,3}"
         r"| ?[^\s\p{L}\p{N}]+[\r\n]*|\s*[\r\n]+|\s+(?!\S)|\s+")
# Bytes of sample text per source group; curated is split evenly over s4_clean sources.
SAMPLE_BYTES = {"fineweb2_vie": 900e6, "culturax_vi": 300e6, "owner_books": 200e6, "curated": 600e6}
HELDOUT = corpus.load_config("data-mix.yaml")["heldout_fraction"]


def sample_texts(source_id, max_bytes):
    taken = 0
    for doc_id, text in corpus.iter_docs(source_id):
        if corpus.is_heldout(doc_id, HELDOUT):
            continue
        yield text
        taken += len(text.encode())
        if taken >= max_bytes:
            return


def training_texts():
    curated = corpus.curated_sources()
    plan = [(s, b) for s, b in SAMPLE_BYTES.items() if s != "curated"]
    plan += [(s, SAMPLE_BYTES["curated"] / len(curated)) for s in curated]
    for source_id, max_bytes in plan:
        print(f"sample {source_id}: ≤{max_bytes / 1e6:.0f} MB", file=sys.stderr, flush=True)
        yield from sample_texts(source_id, max_bytes)


def new_tokenizer():
    tok = Tokenizer(models.BPE())
    tok.pre_tokenizer = pre_tokenizers.Sequence([
        pre_tokenizers.Split(Regex(SPLIT), behavior="isolated"),
        pre_tokenizers.ByteLevel(add_prefix_space=False, use_regex=False)])
    tok.decoder = decoders.ByteLevel()
    return tok


def train(texts, vocab_size=VOCAB_SIZE):
    tok = new_tokenizer()
    trainer = trainers.BpeTrainer(vocab_size=vocab_size, special_tokens=[corpus.EOT], min_frequency=2,
                                  initial_alphabet=pre_tokenizers.ByteLevel.alphabet())
    tok.train_from_iterator(texts, trainer=trainer)
    return tok


def token_byte_lengths(tok):
    """UTF-8 bytes each token id stands for (0 for special tokens): the denominator of bits-per-byte.
    In a byte-level vocabulary every character of a token string encodes exactly one byte."""
    specials = {t.content for t in tok.get_added_tokens_decoder().values()}
    vocab = tok.get_vocab()
    lengths = [0] * tok.get_vocab_size()
    for piece, i in vocab.items():
        lengths[i] = 0 if piece in specials else len(piece)
    return lengths


def fertility(docs_per_domain=300):
    from transformers import AutoTokenizer
    lab = Tokenizer.from_file(str(corpus.TOKENIZER_PATH))
    qwen = AutoTokenizer.from_pretrained("Qwen/Qwen2.5-0.5B-Instruct")
    by_domain = {}
    for source_id, domain in corpus.domain_map().items():
        by_domain.setdefault(domain, []).append(source_id)
    report = {}
    for domain, sources in sorted(by_domain.items()):
        texts = []
        for s in sources:
            try:
                held = (t for d, t in corpus.iter_docs(s) if corpus.is_heldout(d, HELDOUT))
                texts += list(itertools.islice(held, docs_per_domain // len(sources) + 1))
            except FileNotFoundError:
                continue
        syllables = sum(len(t.split()) for t in texts)
        if not syllables:
            continue
        lab_n = sum(len(e.ids) for e in lab.encode_batch(texts))
        qwen_n = sum(len(ids) for ids in qwen(texts)["input_ids"])
        report[domain] = {"syllables": syllables, "lab": round(lab_n / syllables, 3),
                          "qwen2.5": round(qwen_n / syllables, 3)}
        print(domain, report[domain], flush=True)
    out = corpus.TOKENIZER_PATH.with_suffix(".fertility.json")
    out.write_text(json.dumps(report, indent=1), encoding="utf-8")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["train", "fertility"])
    args = parser.parse_args()
    if args.command == "fertility":
        return fertility()
    corpus.TOKENIZER_PATH.parent.mkdir(parents=True, exist_ok=True)
    tok = train(training_texts())
    tmp = corpus.TOKENIZER_PATH.with_suffix(".tmp")
    tok.save(str(tmp))
    tmp.replace(corpus.TOKENIZER_PATH)
    print(f"saved {corpus.TOKENIZER_PATH} vocab={tok.get_vocab_size()}")


if __name__ == "__main__":
    main()
