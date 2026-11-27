"""
Vietnamese syllable dictionary and the text-quality score built on it (phase 7: rate extracted and
OCR text by the share of valid syllables; a broken font map or a bad OCR layer drives it down).

    uv run python -m pipeline.books.syllables build   # regenerate vi_syllables.txt from the corpus

The list is every lower-case token with a Vietnamese-only letter or made of plain letters that occurs
at least MIN_COUNT times in clean edited sources (Wikipedia and two national newspapers after s4).
Foreign names that slip in are harmless: the score only needs bad text to stand out from good text.
"""
import re
import sys
import unicodedata
from collections import Counter
from functools import cache
from pathlib import Path

VOCAB_PATH = Path(__file__).with_name("vi_syllables.txt")
SOURCES = ("vi_wikipedia_org", "tuoitre_vn", "nhandan_vn")
DOCS_PER_SOURCE = 60_000
MIN_COUNT = 20
_TOKEN = re.compile(r"[^\W\d_]+")
_VI_ONLY = set("ăâđêôơưàáảãạằắẳẵặầấẩẫậèéẻẽẹềếểễệìíỉĩịòóỏõọồốổỗộờớởỡợùúủũụừứửữựỳýỷỹỵ")


@cache
def vocab():
    return frozenset(VOCAB_PATH.read_text(encoding="utf-8").split())


def tokens(text):
    return _TOKEN.findall(unicodedata.normalize("NFC", text).lower())


def vi_score(text):
    """(share of tokens that are known syllables, share of tokens with a Vietnamese-only letter)."""
    toks = tokens(text)
    if not toks:
        return None, 0.0
    known = vocab()
    return sum(t in known for t in toks) / len(toks), sum(any(c in _VI_ONLY for c in t) for t in toks) / len(toks)


def build():
    import pyarrow.parquet as pq

    from pipeline.paths import PROCESSED_DIR
    counts = Counter()
    for source in SOURCES:
        seen = 0
        for path in sorted((PROCESSED_DIR / "s4_clean" / source).rglob("*.parquet")):
            for batch in pq.ParquetFile(path).iter_batches(columns=["text"], batch_size=2000):
                for text in batch.column(0).to_pylist():
                    counts.update(tokens(text))
                seen += batch.num_rows
                if seen >= DOCS_PER_SOURCE:
                    break
            if seen >= DOCS_PER_SOURCE:
                break
        print(source, seen, file=sys.stderr)
    words = sorted(t for t, n in counts.items() if n >= MIN_COUNT and len(t) <= 7)
    VOCAB_PATH.write_text("\n".join(words) + "\n", encoding="utf-8")
    return len(words)


if __name__ == "__main__" and sys.argv[1:] == ["build"]:
    print(build(), "syllables ->", VOCAB_PATH)
