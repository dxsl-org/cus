"""
Import public eval sets (downloaded by pipeline.acquire.hf_snapshot) as pending eval items.

    uv run python -m taskset.import_eval vmlu viquad2 global_mmlu humaneval mbpp [--db PATH]

Only splits with public answers are imported (VMLU and ViQuAD hide their test answers):
  VMLU valid.jsonl -> eval_mcq; ViQuAD 2.0 validation answerable questions -> eval_open;
  Global-MMLU test: every vi question, and for the other languages one hash-chosen sample of question ids
  (contained in vi, so per-language scores compare the same questions) -> eval_mcq;
  HumanEval test, MBPP sanitized test -> eval_code (English prompts; Vietnamese
  translations are authored separately as new items).
Items land in split `test` as `pending`: a public benchmark is not trusted until reviewed.
Item ids are "<dataset>:<dataset id>", so re-running only adds what is missing.
"""
import argparse
import ast
import hashlib
import json
import re
import sys

import pyarrow.parquet as pq

from pipeline.acquire.hf_snapshot import load_declarations, target_dir
from taskset import content as tc
from taskset import store

_LABEL = re.compile(r"^\s*([A-J])\s*[.)]\s*")
BY = "import"
# Global-MMLU sample: first N question ids by salted hash; vi takes every question (None), so it contains the
# others' samples. evalset-v1 froze 300 vi items; evalset-v2 scores all 14,042 to separate close models.
GMMLU_SALT = "llm-evalset/global-mmlu/v1"
GMMLU_SAMPLE = {"vi": None, "en": 150, "zh": 150, "ja": 150, "ko": 150, "fr": 150, "de": 150, "es": 150, "ru": 150}
GMMLU_DOMAIN = {"STEM": "science", "Business": "economics", "Medical": "health", "Social Sciences": "society",
                "Humanities": "culture_history", "Other": "general"}
CODE_INSTRUCTION = ("Complete the following Python task. Reply with the complete code (imports and function) "
                    "in a single ```python block.")


def vmlu_items(directory):
    for line in (directory / "valid.jsonl").read_text(encoding="utf-8").splitlines():
        row = json.loads(line)
        labels = [_LABEL.match(c) for c in row["choices"]]
        if not all(labels) or "".join(m[1] for m in labels) != "ABCDEFGHIJ"[:len(labels)]:
            raise ValueError(f"vmlu {row['id']}: unexpected choice labels {row['choices']}")
        choices = [_LABEL.sub("", c, count=1).strip() for c in row["choices"]]
        prompt = row["question"].strip() + "\n" + "\n".join(f"{m[1]}. {c}" for m, c in zip(labels, choices))
        yield row["id"], "eval_mcq", {
            "domain": "general", "skills": ["recall"], "lang": "vi",
            "messages": [{"role": "user", "content": prompt}],
            "choices": choices, "answer_key": row["answer"], "verifier": {"type": "choice"},
            "meta": {"external_id": row["id"], "subject_code": row["id"].split("-")[0]},
        }


def viquad2_items(directory):
    table = pq.read_table(directory / "data" / "validation-00000-of-00001.parquet")
    for row in table.to_pylist():
        if row["is_impossible"] or not row["answers"]:
            continue
        answers = list(dict.fromkeys(a.strip() for a in row["answers"]["text"] if a.strip()))
        if not answers:
            continue
        prompt = (f"Đọc đoạn văn sau và trả lời câu hỏi bằng một cụm từ trích từ đoạn văn.\n\n"
                  f"{row['title']}\n{row['context'].strip()}\n\nCâu hỏi: {row['question'].strip()}")
        yield row["id"], "eval_open", {
            "domain": "general", "skills": ["comprehension", "extraction"], "lang": "vi",
            "messages": [{"role": "user", "content": prompt}], "reference_answer": answers,
            "verifier": {"type": "exact", "normalize": ["nfc", "lower", "strip_punct"]},
            "meta": {"external_id": row["id"], "uit_id": row["uit_id"]},
        }


def _mcq_prompt(question, choices):
    return question.strip() + "\n" + "\n".join(f"{'ABCD'[i]}. {c}" for i, c in enumerate(choices))


def global_mmlu_items(directory):
    for lang, n in GMMLU_SAMPLE.items():
        rows = pq.read_table(directory / lang / "test-00000-of-00001.parquet").to_pylist()
        rows.sort(key=lambda r: hashlib.sha256(f"{GMMLU_SALT}:{r['sample_id']}".encode()).hexdigest())
        for row in rows[:n]:  # n None: every question (vi, for evalset-v2's full vi_mmlu group)
            choices = [str(row[f"option_{x}"]).strip() for x in "abcd"]
            yield f"{lang}:{row['sample_id']}", "eval_mcq", {
                "domain": GMMLU_DOMAIN[row["subject_category"]], "skills": ["recall"], "lang": lang,
                "messages": [{"role": "user", "content": _mcq_prompt(row["question"], choices)}],
                "choices": choices, "answer_key": row["answer"], "verifier": {"type": "choice"},
                "meta": {"external_id": row["sample_id"], "subject": row["subject"],
                         "subject_category": row["subject_category"],
                         "cultural_sensitivity": row["cultural_sensitivity_label"]},
            }


def _code_item(prompt, reference, test, timeout=10, setup=""):
    spec = {"language": "python", "test": test, "timeout": timeout, **({"setup": setup} if setup else {})}
    return {
        "domain": "programming", "skills": ["coding"], "lang": "en",
        "messages": [{"role": "user", "content": f"{CODE_INSTRUCTION}\n\n{prompt.strip()}"}],
        "reference_answer": reference,
        "verifier": {"type": "unit_test", "spec": spec},
    }


def humaneval_items(directory):
    for row in pq.read_table(directory / "openai_humaneval" / "test-00000-of-00001.parquet").to_pylist():
        # The prompt's own imports (e.g. `from typing import List`) are given, as in the original harness.
        setup = "\n".join(x for x in row["prompt"].splitlines() if x.startswith(("import ", "from ")))
        item = _code_item(f"```python\n{row['prompt']}```", row["prompt"] + row["canonical_solution"],
                          f"{row['test']}\n\ncheck({row['entry_point']})\n", setup=setup)
        item["meta"] = {"external_id": row["task_id"], "entry_point": row["entry_point"]}
        yield row["task_id"].replace("HumanEval/", ""), "eval_code", item


def _as_list(value):
    return list(value) if not isinstance(value, str) else ast.literal_eval(value)


def mbpp_items(directory):
    for row in pq.read_table(directory / "sanitized" / "test-00000-of-00001.parquet").to_pylist():
        tests = _as_list(row["test_list"])
        test_code = "\n".join([*_as_list(row["test_imports"]), *tests]) + "\n"
        prompt = f"{row['prompt'].strip()}\nYour code must pass these tests:\n" + "\n".join(tests)
        item = _code_item(prompt, row["code"], test_code)
        item["meta"] = {"external_id": str(row["task_id"])}
        yield str(row["task_id"]), "eval_code", item


READERS = {"vmlu": vmlu_items, "viquad2": viquad2_items, "global_mmlu": global_mmlu_items,
           "humaneval": humaneval_items, "mbpp": mbpp_items}


def import_dataset(con, name):
    """
    Add missing items. An existing item whose imported content changed (an importer fix) gets a
    new version if still pending; reviewed items are left alone and counted, never silently reset.
    """
    decl = load_declarations()[name]
    store.add_source(con, name, decl["license"], decl["usage_scope"], note=f"{decl['repo']}@{decl['revision']}")
    existing = {r["id"]: r for r in con.execute(
        "SELECT i.id, i.status, v.content_sha256 FROM items i JOIN item_versions v ON v.id = i.current_version_id "
        "WHERE i.source_id = ?", (name,))}
    stats = {"added": 0, "updated": 0, "unchanged": 0, "changed_but_reviewed": 0}
    for external_id, kind, data in READERS[name](target_dir(decl)):
        item_id = f"{name}:{external_id}"
        row = existing.get(item_id)
        if row is None:
            store.create_item(con, kind, name, data, f"import:{name}", BY, item_id=item_id)
            store.submit(con, item_id, BY)
            stats["added"] += 1
        elif tc.canonical(data)[1] == row["content_sha256"]:
            stats["unchanged"] += 1
        elif row["status"] == "pending":
            store.edit_content(con, item_id, data, f"import:{name}", BY)
            stats["updated"] += 1
        else:
            stats["changed_but_reviewed"] += 1
    return stats


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("datasets", nargs="+", choices=sorted(READERS))
    parser.add_argument("--db", default=store.DEFAULT_DB)
    args = parser.parse_args()
    con = store.connect(args.db)
    for name in args.datasets:
        print(f"{name}: {import_dataset(con, name)}")


if __name__ == "__main__":
    main()
