"""
13-gram decontamination against frozen eval sets.

The index holds every 13-gram (lowercased \\w+ tokens, NFC) of every text in
DATA_ROOT/eval/<evalset>/decontam.jsonl: questions, options and reference answers of test and
holdout items. Grounding passages (e.g. law articles a question was written from) are NOT in that
file on purpose: removing them would strip the law itself from training and make the law group
of the eval measure nothing.

A document contaminates an item when it contains at least MIN_COVERAGE of the item's distinct
13-grams. A single shared 13-gram is not enough: law questions quote stock phrases ("Theo Bộ luật
Hình sự số 100/2015/QH13 (đã được sửa đổi, bổ sung bởi ...") and decree wording that thousands of
rulings and statutes contain; any-hit matching dropped 1,017 documents for one such question
(2026-11-15). A page that leaks a question copies its stem and options, i.e. most of its grams.
"""
import json
import re
from collections import Counter, defaultdict

from pipeline.paths import DATA_ROOT
from pipeline.process.normalize import nfc

NGRAM = 13
MIN_COVERAGE = 0.5
_WORD = re.compile(r"\w+", re.UNICODE)


def ngrams(text, n=NGRAM):
    words = [w.lower() for w in _WORD.findall(nfc(text))]
    return {" ".join(words[i:i + n]) for i in range(len(words) - n + 1)}


def frozen_evalsets():
    """Every built eval set (a directory with decontam.jsonl): all of them stay out of every training corpus."""
    return tuple(sorted(p.parent.name for p in (DATA_ROOT / "eval").glob("*/decontam.jsonl")))


class EvalIndex:
    """13-gram -> eval item ids, plus each item's number of distinct 13-grams."""

    def __init__(self, evalsets=None):
        """evalsets: names under DATA_ROOT/eval; None = every frozen eval set."""
        self.evalsets = tuple(evalsets) if evalsets else frozen_evalsets()
        self.owners, self.size = defaultdict(list), {}
        for evalset in self.evalsets:
            with open(DATA_ROOT / "eval" / evalset / "decontam.jsonl", encoding="utf-8") as fh:
                for line in fh:
                    entry = json.loads(line)
                    self.add(entry["id"], entry["texts"])

    def add(self, item_id, texts):
        """Index an item once: eval sets share items (v2 keeps v1's), and listing an item twice would count its
        13-grams twice in matches(), halving the coverage threshold."""
        if item_id in self.size:
            return
        grams = set().union(*(ngrams(t) for t in texts)) if texts else set()
        if grams:
            self.size[item_id] = len(grams)
            for gram in grams:
                self.owners[gram].append(item_id)

    def matches(self, text):
        """Eval item ids whose 13-grams `text` covers at least MIN_COVERAGE of (empty set when clean)."""
        hits = Counter(i for g in ngrams(text) if g in self.owners for i in self.owners[g])
        return {i for i, n in hits.items() if n / self.size[i] >= MIN_COVERAGE}
