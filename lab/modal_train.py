"""
Run lab training on Modal GPUs, capped by a local cost ledger so usage stays inside the free monthly credit.

  uv run --group lab --group cloud python -m lab.modal_train bench <config> [--gpu H100] [--micro-batch 16]
  uv run --group lab --group cloud python -m lab.modal_train train <config> [<config> ...] [--gpu H100] [--hours 3]
  uv run --group lab --group cloud python -m lab.modal_train fetch <run>
  uv run --group lab --group cloud python -m lab.modal_train spent

Data lives on the Modal Volume `llm-lab` as LLM_DATA_ROOT/lab (tokenizer, token shards, runs); missing files and
the init_from base checkpoints are uploaded before each launch. Several configs in one `train` call run in
parallel, one GPU each. Finished runs are downloaded into the local RUNS_DIR.

Budget contract: spent = Modal's metered cost this month (`modal billing summary`) + the reservation (rate ×
timeout) of every call not yet returned + the measured cost of calls returned in the last hour (Modal's meter can
lag; counting them twice only overestimates). A launch cuts the container timeout so its reservation fits
BUDGET_USD − RESERVE_USD − spent. The workspace spend limit $0 (Modal → Settings → Usage & Billing) is the hard
backstop: no charge beyond the credit.

Cloud runs freeze the config as launched (with ckpt_minutes 10 and any --micro-batch override), so resume
them on Modal, not locally.
"""
import argparse
import json
import pathlib
import subprocess
import sys
import time
import uuid

import modal
import yaml

from lab import corpus

BUDGET_USD = 30.0  # workspace credit per the dashboard: $8.34 used + $21.66 left (2026-11-20)
RESERVE_USD = 1.5  # image builds and CPU/memory above the request (volume storage is within the free allowance)
# Modal list prices (USD/s, 2026-11) and dense bf16 peaks for MFU.
GPUS = {"H100": (0.001097, 989e12), "A100-80GB": (0.000694, 312e12), "L40S": (0.000542, 362e12)}
CPU, MEMORY_GIB = 4, 16
HOST_RATE = CPU * 0.0000131 + MEMORY_GIB * 0.00000222
MAX_TIMEOUT_S = 24 * 3600
STARTUP_S = 120  # container boot before train_remote starts its clock
UNMETERED_WINDOW_S = 3600
REMOTE_ROOT = "/data"
LEDGER = corpus.LAB_DATA / "modal" / "ledger.jsonl"
REPO = pathlib.Path(__file__).resolve().parents[1]

image = (
    modal.Image.debian_slim(python_version="3.12")
    .apt_install("build-essential")  # Triton compiles its launcher with gcc
    # torch 2.12 pins Triton 3.7.1, which fla needs for Gated DeltaNet on Hopper (fla #640); mirrors uv.lock.
    .pip_install("torch==2.12.1", "flash-linear-attention==0.6.0", "tokenizers==0.23.3", "numpy==2.5.4",
                 "pyyaml==6.0.3", "pyarrow==26.0.1", extra_index_url="https://download.pytorch.org/whl/cu130")
    .env({"LLM_DATA_ROOT": REMOTE_ROOT})
    .add_local_dir(REPO / "lab", "/root/lab", ignore=["**/__pycache__/**"])
    .add_local_file(REPO / "pipeline" / "paths.py", "/root/pipeline/paths.py")
)
volume = modal.Volume.from_name("llm-lab", create_if_missing=True, version=2)
app = modal.App("llm-lab", image=image)


@app.function(gpu="H100", cpu=CPU, memory=MEMORY_GIB * 1024, volumes={REMOTE_ROOT: volume}, timeout=3600)
def train_remote(config_text, peak_flops, max_steps=None):
    """Train one config (YAML text). Token shards are copied to local disk first: random window reads from the
    volume would be slower. Returns the process exit code and the last log record."""
    import os
    import shutil
    import subprocess
    import sys

    start = time.time()
    shutil.copytree(f"{REMOTE_ROOT}/lab/tokens/lab-vi-32k", "/tmp/tokens", dirs_exist_ok=True)
    print(f"copied token shards in {time.time() - start:.0f}s", flush=True)
    raw = yaml.safe_load(config_text)
    config = pathlib.Path(f"/tmp/{raw['train']['name']}.yaml")
    config.write_text(config_text, encoding="utf-8")
    cmd = [sys.executable, "-u", "-m", "lab.train", str(config), "--tokens-dir", "/tmp/tokens"]
    if max_steps:
        cmd += ["--max-steps", str(max_steps)]
    code = subprocess.run(cmd, cwd="/root", env=os.environ | {"LAB_PEAK_FLOPS": str(peak_flops)}).returncode
    log = pathlib.Path(f"{REMOTE_ROOT}/lab/runs/{raw['train']['name']}/log.jsonl")
    records = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()] if log.exists() else []
    volume.commit()
    return {"code": code, "records": records, "seconds": round(time.time() - start)}


@app.function(volumes={REMOTE_ROOT: volume}, timeout=600)
def remove_run(name):
    import shutil
    shutil.rmtree(f"{REMOTE_ROOT}/lab/runs/{name}", ignore_errors=True)
    volume.commit()


def ledger_entries():
    if not LEDGER.exists():
        return {}
    entries = {}
    for line in LEDGER.read_text(encoding="utf-8").splitlines():
        rec = json.loads(line)
        entries.setdefault(rec["id"], {}).update(rec)
    return entries


def metered_usd():
    out = subprocess.run([sys.executable, "-m", "modal", "billing", "summary", "--json"], capture_output=True,
                         text=True, check=True).stdout
    return float(json.loads(out)["metered_cost"])


def spent():
    """USD counted against the budget (see the module docstring)."""
    now = time.time()
    pending = 0.0
    for e in ledger_entries().values():
        if "elapsed_s" not in e:
            pending += e["rate_s"] * e["reserved_s"]
        elif now - e.get("end", 0) < UNMETERED_WINDOW_S:
            pending += e["rate_s"] * e["elapsed_s"]
    return metered_usd() + pending


def ledger_write(rec):
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    with open(LEDGER, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec) + "\n")


def remote_sizes(prefix):
    try:
        return {e.path.lstrip("/"): e.size for e in volume.listdir(prefix, recursive=True)}
    except modal.exception.NotFoundError:
        return {}


def upload_missing(local_dir, remote_dir, names=None):
    """Uploads files of local_dir absent from the volume or of a different size."""
    have = remote_sizes(remote_dir)
    todo = [p for p in sorted(local_dir.iterdir()) if p.is_file() and (names is None or p.name in names)
            and have.get(f"{remote_dir}/{p.name}") != p.stat().st_size]
    if todo:
        print(f"uploading {len(todo)} files ({sum(p.stat().st_size for p in todo) / 1e9:.2f} GB) to {remote_dir}")
        with volume.batch_upload(force=True) as batch:
            for p in todo:
                batch.put_file(p, f"/{remote_dir}/{p.name}")


def sync(raws):
    upload_missing(corpus.TOKENIZER_PATH.parent, "lab/tokenizer")
    upload_missing(corpus.TOKENS_DIR, "lab/tokens/lab-vi-32k")
    for r in raws:
        if base := r["train"].get("init_from"):
            ckpt = r["train"].get("init_ckpt", "ckpt.pt")
            upload_missing(corpus.RUNS_DIR / base, f"lab/runs/{base}", names={"config.yaml", ckpt})


def fetch(name):
    local = corpus.RUNS_DIR / name
    local.mkdir(parents=True, exist_ok=True)
    for path in remote_sizes(f"lab/runs/{name}"):
        tmp = local / (pathlib.Path(path).name + ".part")
        with open(tmp, "wb") as f:
            for chunk in volume.read_file(path):
                f.write(chunk)
        tmp.replace(local / pathlib.Path(path).name)
    print(f"fetched {name} -> {local}")


def cloud_config(path, micro_batch, name=None):
    raw = yaml.safe_load(pathlib.Path(path).read_text(encoding="utf-8"))
    raw["train"]["ckpt_minutes"] = 10  # a preemption or the budget timeout loses at most 10 minutes
    if micro_batch:
        raw["train"]["micro_batch"] = micro_batch
    if name:
        raw["train"]["name"] = name
    return raw


def launch(raws, gpu, hours, max_steps=None, cleanup=False):
    """Runs the configs in parallel; returns their results. Refuses when the budget cannot cover 10 minutes.
    cleanup deletes the runs from the volume afterwards (benchmarks)."""
    gpu_rate, peak = GPUS[gpu]
    rate_s = gpu_rate + HOST_RATE
    left = BUDGET_USD - RESERVE_USD - spent()
    timeout = int(min(hours * 3600, left / (rate_s * len(raws)), MAX_TIMEOUT_S))
    if timeout < 600:
        raise SystemExit(f"budget left ${left:.2f} cannot cover 10 minutes of {len(raws)}× {gpu}")
    print(f"{len(raws)}× {gpu} at ${rate_s * 3600:.2f}/h each, timeout {timeout / 3600:.2f} h, "
          f"budget left ${left:.2f} (reserving ${rate_s * timeout * len(raws):.2f})")
    sync(raws)
    ids = [str(uuid.uuid4()) for _ in raws]
    for i, raw in zip(ids, raws):
        ledger_write({"id": i, "run": raw["train"]["name"], "gpu": gpu, "rate_s": rate_s, "reserved_s": timeout,
                      "start": time.strftime("%Y-%m-%d %H:%M:%S")})
    fn = train_remote.with_options(gpu=gpu, timeout=timeout)
    start = time.time()
    with modal.enable_output(), app.run(detach=True):
        calls = [fn.spawn(yaml.safe_dump(raw, allow_unicode=True, sort_keys=False), peak, max_steps) for raw in raws]
        results = []
        for i, call in zip(ids, calls):
            elapsed = None
            try:
                result = call.get()
                elapsed = result["seconds"] + STARTUP_S
                results.append(result)
            finally:  # a failed get() keeps the spawn-to-now wall time, an overestimate
                ledger_write({"id": i, "elapsed_s": elapsed or round(time.time() - start), "end": time.time()})
        if cleanup:
            for raw in raws:
                remove_run.remote(raw["train"]["name"])
    print(f"spent so far ${spent():.2f} of ${BUDGET_USD:.2f}")
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    for name in ("bench", "train"):
        p = sub.add_parser(name)
        p.add_argument("configs", nargs="+" if name == "train" else 1)
        p.add_argument("--gpu", default="H100", choices=sorted(GPUS))
        p.add_argument("--micro-batch", type=int)
        p.add_argument("--hours", type=float, default=0.5 if name == "bench" else 3)
    sub.add_parser("fetch").add_argument("run")
    sub.add_parser("spent")
    args = parser.parse_args()

    if args.cmd == "spent":
        for e in ledger_entries().values():
            state = f"{e['elapsed_s']}s" if "elapsed_s" in e else f"unfinished, {e['reserved_s']}s reserved"
            print(f"{e['start']} {e['run']} {e['gpu']} {state}: ~${e['rate_s'] * e.get('elapsed_s', e['reserved_s']):.2f}")
        print(f"Modal metered ${metered_usd():.2f}; counted against the budget ${spent():.2f} of ${BUDGET_USD:.2f}")
    elif args.cmd == "fetch":
        fetch(args.run)
    elif args.cmd == "bench":
        mb = args.micro_batch
        name = f"bench-{pathlib.Path(args.configs[0]).stem}-{args.gpu.lower()}-mb{mb or 'cfg'}"
        raw = cloud_config(args.configs[0], mb, name=name)
        (result,) = launch([raw], args.gpu, args.hours, max_steps=30, cleanup=True)
        tok_s = sorted(r["tok_s"] for r in result["records"][5:])  # first steps include compilation
        print(f"exit {result['code']}; median {tok_s[len(tok_s) // 2] if tok_s else '?'} tok/s on {args.gpu}")
    else:
        raws = [cloud_config(c, args.micro_batch) for c in args.configs]
        for raw, result in zip(raws, launch(raws, args.gpu, args.hours)):
            last = result["records"][-1] if result["records"] else {}
            print(f"{raw['train']['name']}: exit {result['code']}, step {last.get('step')}, bpb {last.get('bpb/all')}")
            if result["code"] == 0:
                fetch(raw["train"]["name"])


if __name__ == "__main__":
    main()
