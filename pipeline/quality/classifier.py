"""
q_edu: a fast regressor of the LLM's 0-5 knowledge/information score (phase 6 step 7).

    uv run --group process python -m pipeline.quality.classifier train [--labels FILE]
    uv run --group process python -m pipeline.quality.classifier check <hand-labelled csv>

Features: hashed syllable unigrams + bigrams (lowercased) of the first DOC_CHARS characters,
sublinear TF, L2-normalised; model: ridge regression. Chosen over a GPU embedding model because
it must score ~10^8 documents on CPU in hours, not days (same trade-off as DCLM's fastText filter).
Training holds out HOLDOUT_FRACTION of the labels (by doc_id hash) and reports Spearman and
binary F1 at score >= 3 on it; the model and report go to DATA_ROOT/quality/.
"""
import argparse
import csv
import hashlib
import json
import pickle
import sys

import numpy as np
from scipy.stats import spearmanr
from sklearn.feature_extraction.text import HashingVectorizer, TfidfTransformer
from sklearn.linear_model import Ridge
from sklearn.metrics import f1_score
from sklearn.pipeline import make_pipeline

from pipeline.paths import DATA_ROOT

DOC_CHARS = 3000
HOLDOUT_FRACTION = 0.1
MODEL_PATH = DATA_ROOT / "quality" / "q_edu.pkl"


def _vectorizer():
    return HashingVectorizer(n_features=2 ** 21, ngram_range=(1, 2), lowercase=True, alternate_sign=False,
                             norm=None, token_pattern=r"(?u)\b\w+\b")


def build():
    return make_pipeline(_vectorizer(), TfidfTransformer(sublinear_tf=True), Ridge(alpha=1.0))


def _is_holdout(doc_id):
    return int(hashlib.sha256(f"q_edu-holdout:{doc_id}".encode()).hexdigest()[:8], 16) / 2 ** 32 < HOLDOUT_FRACTION


def _report(y_true, y_pred):
    y_true, y_pred = np.asarray(y_true, float), np.clip(np.asarray(y_pred, float), 0, 5)
    return {"n": int(len(y_true)), "spearman": round(float(spearmanr(y_true, y_pred).statistic), 4),
            "mae": round(float(np.abs(y_true - y_pred).mean()), 4),
            "f1_at_3": round(float(f1_score(y_true >= 3, y_pred >= 3)), 4)}


def train(labels_path, sample_path):
    text = {d["doc_id"]: d["text"][:DOC_CHARS] for d in map(json.loads, open(sample_path, encoding="utf-8"))}
    rows = [json.loads(x) for x in open(labels_path, encoding="utf-8")]
    rows = [r for r in rows if r["score"] is not None and r["doc_id"] in text]
    train_rows = [r for r in rows if not _is_holdout(r["doc_id"])]
    test_rows = [r for r in rows if _is_holdout(r["doc_id"])]
    model = build().fit([text[r["doc_id"]] for r in train_rows], [r["score"] for r in train_rows])
    report = {"train": len(train_rows), "holdout": _report([r["score"] for r in test_rows],
                                                           model.predict([text[r["doc_id"]] for r in test_rows])),
              "label_distribution": {str(k): int(v) for k, v in zip(*np.unique([r["score"] for r in rows],
                                                                                 return_counts=True))}}
    MODEL_PATH.write_bytes(pickle.dumps(model))
    (MODEL_PATH.with_suffix(".report.json")).write_text(json.dumps(report, indent=1), encoding="utf-8")
    return report


def load():
    return pickle.loads(MODEL_PATH.read_bytes())


def score(model, texts):
    """q_edu in [0, 5] for each text."""
    return np.clip(model.predict([t[:DOC_CHARS] for t in texts]), 0, 5)


def check(csv_path):
    """Agreement with hand labels: CSV with columns text, diem (0-5)."""
    with open(csv_path, encoding="utf-8-sig", newline="") as fh:
        rows = [r for r in csv.DictReader(fh) if r.get("diem", "").strip().isdigit()]
    return _report([int(r["diem"]) for r in rows], score(load(), [r["text"] for r in rows]))


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["train", "check"])
    parser.add_argument("path", nargs="?")
    parser.add_argument("--labels", default=str(DATA_ROOT / "quality" / "labels-Qwen2.5-7B-Instruct.jsonl"))
    parser.add_argument("--sample", default=str(DATA_ROOT / "quality" / "label_sample.jsonl"))
    args = parser.parse_args()
    result = train(args.labels, args.sample) if args.command == "train" else check(args.path)
    print(json.dumps(result, indent=1))


if __name__ == "__main__":
    main()
