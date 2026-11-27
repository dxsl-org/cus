"""
Evaluate and calibrate q_edu against human-annotated FineWeb-C (vie_Latn).

    uv run --group process python -m pipeline.quality.eval_fineweb_c

Runs purely on CPU (~1-2 min). Never uses GPU. Keeps existing q_edu.pkl untouched
unless --apply is passed (or an external driver explicitly promotes a candidate).
"""
import argparse
import csv
import json
import sys
import time
from pathlib import Path

import numpy as np
import pyarrow.parquet as pq
from scipy.stats import spearmanr
from sklearn.feature_extraction.text import HashingVectorizer, TfidfTransformer
from sklearn.linear_model import Ridge
from sklearn.metrics import f1_score
from sklearn.model_selection import KFold
from sklearn.pipeline import FeatureUnion, make_pipeline

from pipeline.paths import DATA_ROOT, RAW_DIR
from pipeline.quality import classifier

# Map FineWeb-C human labels onto the 0-5 educational rubric scale of q_edu.
# "Problematic Content" is treated as 0 (matches rubric rule: 0 for spam/harmful).
FWC_SCORE_MAP = {
    "None": 0.0,
    "Minimal": 1.0,
    "Basic": 2.0,
    "Good": 3.5,
    "Excellent": 5.0,
    "❗ Problematic Content ❗": 0.0,
}


def load_fineweb_c(parquet_dir=None):
    base = Path(parquet_dir) if parquet_dir else RAW_DIR / "open_datasets" / "fineweb_c_vie" / "vie_Latn"
    files = sorted(base.glob("*.parquet"))
    if not files:
        raise FileNotFoundError(f"No parquet files under {base}")
    rows = []
    for path in files:
        for r in pq.read_table(path).to_pylist():
            labels = r.get("educational_value_labels") or []
            if not labels:
                continue
            nums = [FWC_SCORE_MAP[lbl] for lbl in labels if lbl in FWC_SCORE_MAP]
            if not nums:
                continue
            mean_score = float(np.mean(nums))
            good_votes = sum(1 for lbl in labels if lbl in ("Good", "Excellent"))
            # Majority >= Good (or mean >= 2.5 when 2/3 annotators say Basic/Good+)
            is_good = good_votes >= (len(labels) / 2)
            rows.append({
                "id": r["id"],
                "text": r["text"] or "",
                "labels": labels,
                "score": mean_score,
                "is_good": bool(is_good),
                "has_any_good": good_votes > 0,
                "problematic": bool(r.get("problematic_content_label_present")),
            })
    return rows


def load_llm_dataset(labels_path=None, sample_path=None):
    labels_p = Path(labels_path) if labels_path else DATA_ROOT / "quality" / "labels-Qwen2.5-7B-Instruct.jsonl"
    sample_p = Path(sample_path) if sample_path else DATA_ROOT / "quality" / "label_sample.jsonl"
    text_by_id = {d["doc_id"]: d["text"] for d in map(json.loads, open(sample_p, encoding="utf-8"))}
    train_rows, holdout_rows = [], []
    for line in open(labels_p, encoding="utf-8"):
        r = json.loads(line)
        if r.get("score") is None or r["doc_id"] not in text_by_id:
            continue
        item = {"doc_id": r["doc_id"], "text": text_by_id[r["doc_id"]], "score": float(r["score"])}
        if classifier._is_holdout(r["doc_id"]):
            holdout_rows.append(item)
        else:
            train_rows.append(item)
    return train_rows, holdout_rows


def load_hand_labels(csv_path=None):
    path = Path(csv_path) if csv_path else DATA_ROOT / "quality" / "hand-labels.csv"
    if not path.exists():
        return []
    with open(path, encoding="utf-8-sig", newline="") as fh:
        return [
            {"text": r["text"], "score": float(r["diem"])}
            for r in csv.DictReader(fh)
            if (r.get("diem") or "").strip().isdigit()
        ]


def metrics(y_true, y_pred, is_pos=None, threshold=3.0):
    y_true = np.asarray(y_true, float)
    y_pred = np.clip(np.asarray(y_pred, float), 0.0, 5.0)
    pos_true = np.asarray(is_pos, bool) if is_pos is not None else (y_true >= threshold)
    sp = float(spearmanr(y_true, y_pred).statistic)
    mae = float(np.abs(y_true - y_pred).mean())
    f1_fixed = float(f1_score(pos_true, y_pred >= threshold, zero_division=0))
    best_f1, best_th = 0.0, threshold
    for th in np.linspace(0.5, 4.5, 41):
        f1 = float(f1_score(pos_true, y_pred >= th, zero_division=0))
        if f1 > best_f1:
            best_f1, best_th = f1, float(th)
    return {
        "n": int(len(y_true)),
        "spearman": round(sp, 4),
        "mae": round(mae, 4),
        f"f1_at_{threshold:g}": round(f1_fixed, 4),
        "best_f1": round(best_f1, 4),
        "best_th": round(best_th, 2),
    }


def make_candidate(kind, doc_chars, alpha):
    if kind == "word_1_2":
        vec = HashingVectorizer(n_features=2**21, ngram_range=(1, 2), lowercase=True,
                                alternate_sign=False, norm=None, token_pattern=r"(?u)\b\w+\b")
    elif kind == "word_char":
        w = HashingVectorizer(n_features=2**20, ngram_range=(1, 2), lowercase=True,
                              alternate_sign=False, norm=None, token_pattern=r"(?u)\b\w+\b")
        c = HashingVectorizer(n_features=2**20, analyzer="char_wb", ngram_range=(3, 5), lowercase=True,
                              alternate_sign=False, norm=None)
        vec = FeatureUnion([("word", w), ("char", c)])
    else:
        raise ValueError(kind)
    return make_pipeline(vec, TfidfTransformer(sublinear_tf=True), Ridge(alpha=alpha)), doc_chars


def evaluate():
    fwc = load_fineweb_c()
    llm_train, llm_hold = load_llm_dataset()
    hand = load_hand_labels()
    cur_model = classifier.load()

    fwc_texts_3k = [r["text"][:classifier.DOC_CHARS] for r in fwc]
    t0 = time.perf_counter()
    cur_fwc_pred = np.clip(cur_model.predict(fwc_texts_3k), 0.0, 5.0)
    ms_per_1k = (time.perf_counter() - t0) * 1000.0
    cur_llm_pred = np.clip(cur_model.predict([r["text"][:classifier.DOC_CHARS] for r in llm_hold]), 0.0, 5.0)

    clean_idx = [i for i, r in enumerate(fwc) if not r["problematic"]]
    report = {
        "fineweb_c_summary": {
            "total": len(fwc),
            "majority_good": sum(r["is_good"] for r in fwc),
            "any_good": sum(r["has_any_good"] for r in fwc),
            "problematic": sum(r["problematic"] for r in fwc),
            "hand_labels_filled": len(hand),
        },
        "current_q_edu": {
            "ms_per_1k_docs": round(ms_per_1k, 2),
            "on_llm_holdout": metrics([r["score"] for r in llm_hold], cur_llm_pred),
            "on_fineweb_c_majority_good": metrics([r["score"] for r in fwc], cur_fwc_pred, [r["is_good"] for r in fwc]),
            "on_fineweb_c_any_good": metrics([r["score"] for r in fwc], cur_fwc_pred, [r["has_any_good"] for r in fwc]),
            "on_fineweb_c_non_problematic": metrics(
                [fwc[i]["score"] for i in clean_idx], cur_fwc_pred[clean_idx], [fwc[i]["is_good"] for i in clean_idx]
            ),
        },
        "ablations": [],
    }
    if hand:
        hp = np.clip(cur_model.predict([r["text"][:classifier.DOC_CHARS] for r in hand]), 0.0, 5.0)
        report["current_q_edu"]["on_hand_labels"] = metrics([r["score"] for r in hand], hp)

    configs = [
        ("word_1_2_a1.0_c3000", "word_1_2", 3000, 1.0, 0.0),
        ("word_1_2_a3.0_c3000", "word_1_2", 3000, 3.0, 0.0),
        ("word_1_2_a1.0_c6000", "word_1_2", 6000, 1.0, 0.0),
        ("word_char_a1.0_c3000", "word_char", 3000, 1.0, 0.0),
        ("word_char_a3.0_c3000", "word_char", 3000, 3.0, 0.0),
        ("word_1_2_blend_fwc_w5", "word_1_2", 3000, 1.0, 5.0),
        ("word_char_blend_fwc_w5", "word_char", 3000, 2.0, 5.0),
    ]

    kf = KFold(n_splits=5, shuffle=True, random_state=42)
    fwc_y = np.array([r["score"] for r in fwc])
    fwc_good = np.array([r["is_good"] for r in fwc])
    llm_y_tr = np.array([r["score"] for r in llm_train])
    llm_y_ho = np.array([r["score"] for r in llm_hold])

    for name, kind, doc_chars, alpha, fwc_weight in configs:
        x_llm_tr = [r["text"][:doc_chars] for r in llm_train]
        x_llm_ho = [r["text"][:doc_chars] for r in llm_hold]
        x_fwc = [r["text"][:doc_chars] for r in fwc]

        if fwc_weight == 0.0:
            pipe, _ = make_candidate(kind, doc_chars, alpha)
            pipe.fit(x_llm_tr, llm_y_tr)
            t0 = time.perf_counter()
            fwc_oof = np.clip(pipe.predict(x_fwc), 0.0, 5.0)
            ms_1k = (time.perf_counter() - t0) * 1000.0
            llm_pred = np.clip(pipe.predict(x_llm_ho), 0.0, 5.0)
        else:
            fwc_oof = np.zeros(len(fwc))
            llm_preds = []
            ms_list = []
            for tr_idx, va_idx in kf.split(fwc):
                pipe, _ = make_candidate(kind, doc_chars, alpha)
                x_tr = x_llm_tr + [x_fwc[i] for i in tr_idx]
                y_tr = np.concatenate([llm_y_tr, fwc_y[tr_idx]])
                w_tr = np.concatenate([np.ones(len(x_llm_tr)), np.full(len(tr_idx), fwc_weight)])
                pipe.fit(x_tr, y_tr, ridge__sample_weight=w_tr)
                t0 = time.perf_counter()
                fwc_oof[va_idx] = np.clip(pipe.predict([x_fwc[i] for i in va_idx]), 0.0, 5.0)
                ms_list.append((time.perf_counter() - t0) * 1000.0 * (1000.0 / len(va_idx)))
                llm_preds.append(np.clip(pipe.predict(x_llm_ho), 0.0, 5.0))
            ms_1k = float(np.mean(ms_list))
            llm_pred = np.mean(llm_preds, axis=0)

        report["ablations"].append({
            "name": name,
            "ms_per_1k_docs": round(ms_1k, 2),
            "on_llm_holdout": metrics(llm_y_ho, llm_pred),
            "on_fineweb_c_cv": metrics(fwc_y, fwc_oof, fwc_good),
        })

    out_path = DATA_ROOT / "quality" / "fineweb_c_eval.json"
    out_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    return report


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.parse_args()
    report = evaluate()
    print(json.dumps(report, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
