"""
Publish every lab run's measurements to the Hugging Face dataset DXSL/cus-lab-runs (no weights).

  LLM_DATA_ROOT=E:/llm-data uv run --group lab python -m lab.publish_runs [--dry-run] [--repo DXSL/cus-lab-runs]

Mirrors, per run: config, training log, paired-bpb windows, long-context and expert reports; per frozen evalset
and model: the scored summary and per-item scores. Per-item files keep ids and scores only: question text never
leaves the machine (part of each evalset is an internal holdout), and `generate` outputs are dropped because a
model's answer can restate its question. Also uploads the lab tokenizer and a dataset card
(lab/hf/lab-runs-card.md) pinned to the current git commit. Re-running replaces runs/, eval/ and tokenizer/
on the Hub with the local state.

The token comes from HF_TOKEN or the Windows Credential Manager entry `huggingface`
(`cmdkey /generic:huggingface /user:DXSL /pass:<token>`); it needs write access to the DXSL organization.
"""
import argparse
import csv
import json
import os
import pathlib
import shutil
import subprocess
import tempfile

from lab import corpus
from pipeline.paths import DATA_ROOT, ROOT
from studio.teacher import cred_read

RUN_FILES = ("config.yaml", "log.jsonl", "bpb-windows-256.npz", "long_context.json", "long_context.md",
             "experts.json", "experts.md")
FROZEN_EVALSETS = ("evalset-v1", "evalset-v2")
GENERATE_KEEP = ("id", "group", "lang", "domain", "difficulty", "correct")
CARD = pathlib.Path(__file__).parent / "hf" / "lab-runs-card.md"


def final_eval(log_path):
    """Last log record that carries held-out bpb, or None for runs stopped before their first eval."""
    last = None
    with log_path.open(encoding="utf-8") as f:
        for line in f:
            if '"bpb/all"' in line:
                last = line
    if last is None:
        return None
    record = json.loads(last)
    record.pop("samples", None)
    return record


def stage_runs(out):
    rows = []
    for run_dir in sorted(p for p in corpus.RUNS_DIR.iterdir() if (p / "log.jsonl").is_file()):
        dest = out / "runs" / run_dir.name
        dest.mkdir(parents=True)
        for name in RUN_FILES:
            if (run_dir / name).is_file():
                shutil.copy2(run_dir / name, dest / name)
        record = final_eval(run_dir / "log.jsonl")
        if record:
            rows.append({"run": run_dir.name, **{k: v for k, v in record.items() if k == "step" or k.startswith("bpb/")}})
    columns = ["run", "step", "bpb/all"] + sorted({k for r in rows for k in r} - {"run", "step", "bpb/all"})
    with (out / "summary.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)
    return len(rows)


def stage_eval(out):
    count = 0
    for evalset in FROZEN_EVALSETS:
        for model_dir in sorted((DATA_ROOT / "eval" / "runs" / evalset).iterdir()):
            dest = out / "eval" / evalset / model_dir.name
            for src in sorted(model_dir.glob("*.json*")):
                dest.mkdir(parents=True, exist_ok=True)
                if src.name == "generate.jsonl":
                    with src.open(encoding="utf-8") as f, (dest / src.name).open("w", encoding="utf-8") as g:
                        for line in f:
                            item = json.loads(line)
                            g.write(json.dumps({k: item[k] for k in GENERATE_KEEP if k in item}, ensure_ascii=False) + "\n")
                else:
                    shutil.copy2(src, dest / src.name)
                count += 1
    return count


def stage(out):
    n_runs = stage_runs(out)
    n_eval = stage_eval(out)
    (out / "tokenizer").mkdir()
    shutil.copy2(corpus.TOKENIZER_PATH, out / "tokenizer" / corpus.TOKENIZER_PATH.name)
    commit = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "HEAD"], capture_output=True, text=True,
                            check=True).stdout.strip()
    card = CARD.read_text(encoding="utf-8").replace("{n_runs}", str(n_runs)).replace("{commit}", commit)
    (out / "README.md").write_text(card, encoding="utf-8")
    return n_runs, n_eval, commit


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--repo", default="DXSL/cus-lab-runs")
    parser.add_argument("--dry-run", action="store_true", help="stage into a temp dir and print the plan only")
    args = parser.parse_args()
    with tempfile.TemporaryDirectory() as tmp:
        out = pathlib.Path(tmp)
        n_runs, n_eval, commit = stage(out)
        size = sum(p.stat().st_size for p in out.rglob("*") if p.is_file())
        print(f"{n_runs} runs, {n_eval} eval files, {size / 1e6:.1f} MB, card pinned to {commit[:7]}")
        if args.dry_run:
            return
        from huggingface_hub import HfApi
        token = os.environ.get("HF_TOKEN") or cred_read("huggingface")
        if not token:
            raise SystemExit("no Hugging Face token: set HF_TOKEN or `cmdkey /generic:huggingface /user:DXSL /pass:<token>`")
        api = HfApi(token=token)
        api.create_repo(args.repo, repo_type="dataset", exist_ok=True)
        api.upload_folder(repo_id=args.repo, repo_type="dataset", folder_path=out,
                          delete_patterns=["runs/**", "eval/**", "tokenizer/**"],
                          commit_message=f"Sync lab runs from dxsl-org/cus@{commit[:7]}")
        print(f"https://huggingface.co/datasets/{args.repo}")


if __name__ == "__main__":
    main()
