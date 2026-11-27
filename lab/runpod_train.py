"""
Run lab training on one Runpod pod (H100 SXM, else NVL, else PCIe; Community unless --cloud SECURE), capped by the
account balance.

  uv run --group lab python -m lab.runpod_train up [--hours 12] [--cloud SECURE] [--gpu "H100 PCIe"]
                                                                     create the pod, install, upload data
                                                                     (exit 3: no H100 placeable now, retry)
  uv run --group lab python -m lab.runpod_train bench <config> [--micro-batch 32]
  uv run --group lab python -m lab.runpod_train train <config> [<config> ...] [--micro-batch 32]
  uv run --group lab python -m lab.runpod_train status
  uv run --group lab python -m lab.runpod_train fetch <run> [--no-stable]
  uv run --group lab python -m lab.runpod_train down [--force]

One pod serves every run: Community pods cannot mount network volumes, so the 9 GB of token shards are
uploaded once to the pod's volume disk (/workspace) and reused. `train` queues its configs on the pod and
returns; they run one after another in a detached queue that stops at the first failure, so an unfinished base
run never feeds `init_from`. Re-running `train` with the same configs resumes them from their checkpoints.
`fetch` can download a finished run while the queue trains the next one.

Budget contract: a watchdog on the pod stops it (GPU billing ends, the volume disk stays) when the time in
/workspace/deadline passes. Every command resets the deadline to now + (balance − RESERVE_USD) / pod rate, capped
by --hours from `up`; a queue that ends (done, failed or interrupted) shortens it to IDLE_GRACE_S, so a forgotten
pod idles at most that long. Runpod itself stops pods at a zero balance. `up` restarts a stopped pod (possibly
with zero GPUs if the host is busy, still enough to fetch its runs).

Cloud runs freeze the config as launched (ckpt_minutes 10, any --micro-batch override): resume them on the pod.
"""
import argparse
import json
import pathlib
import sys
import time

import yaml

from lab import corpus
from lab import runpod_api as rp

CLOUDS = ("COMMUNITY", "SECURE")
# Tried in this order on each `up`; dense bf16 peaks are the MFU denominators (SXM matches Modal's).
# PCIe is ~20-30% slower but cheaper per hour, so the total cost stays about the same.
GPUS = {"H100 SXM": 989e12, "H100 NVL": 835e12, "H100 PCIe": 756e12}
NO_CAPACITY = 3  # `up` exit code when every GPU in GPUS was refused: the caller may retry later
IMAGE = "runpod/base:2.1.0-ubuntu2404"  # Runpod's sshd conventions; torch comes from the pip wheel (CUDA 13.0)
VOLUME_GB, DISK_GB = 150, 40  # shards 9 GB + venv ~7 GB + ~10 GB per run (ckpt.pt and ckpt-stable.pt)
RESERVE_USD = 1.0  # volume disk while stopped and a restart to fetch
IDLE_GRACE_S = 45 * 60
STATE = corpus.LAB_DATA / "runpod" / "pod.json"
REPO = pathlib.Path(__file__).resolve().parents[1]
W = "/workspace"
DATA = f"{W}/data/lab"
PINS = ("torch==2.12.1", "flash-linear-attention==0.6.0 tokenizers==0.23.3 numpy==2.5.4 pyyaml==6.0.3 "
        "pyarrow==26.0.1")  # mirrors uv.lock and the Modal image

SETUP = f"""set -e
cd {W}
command -v gcc >/dev/null || (apt-get update -qq && apt-get install -y -qq build-essential)  # Triton launcher
export PATH={W}/bin:$PATH UV_CACHE_DIR=/root/.cache/uv
command -v uv >/dev/null || curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR={W}/bin INSTALLER_NO_MODIFY_PATH=1 sh
if [ "$(cat venv/.pins 2>/dev/null)" != "{' '.join(PINS)}" ]; then
  uv venv --allow-existing venv --python 3.12
  uv pip install --python venv/bin/python {PINS[0]} --index-url https://download.pytorch.org/whl/cu130
  uv pip install --python venv/bin/python {PINS[1]}
  echo "{' '.join(PINS)}" > venv/.pins
fi
cat > env.sh <<'EOF'
export LLM_DATA_ROOT={W}/data LAB_PEAK_FLOPS=@PEAK@ PYTHONUNBUFFERED=1
for v in RUNPOD_POD_ID RUNPOD_API_KEY; do
  kv=$(tr '\\0' '\\n' < /proc/1/environ | grep "^$v=" || true); if [ -n "$kv" ]; then export "$kv"; fi
done
EOF
cat > watchdog.sh <<'EOF'
# Stops the pod once /workspace/deadline (epoch seconds) passes; SIGINT lets lab.train save its checkpoint.
source {W}/env.sh
while true; do
  if [ "$(date +%s)" -ge "$(cat {W}/deadline)" ]; then
    echo "$(date -Is) deadline reached, stopping pod" >> {W}/queue.log
    pkill -INT -f '[l]ab.train'; sleep 120
    curl -s -X POST "https://api.runpod.io/v2/pods/$RUNPOD_POD_ID/action" -H "Authorization: Bearer $RUNPOD_API_KEY" \\
      -H 'Content-Type: application/json' -d '{{"action":"stop"}}'
  fi
  sleep 30
done
EOF
pgrep -f '[w]atchdog.sh' >/dev/null || (setsid nohup bash watchdog.sh > /dev/null 2>&1 &)  # [w]: skip this shell
"""


def state():
    if not STATE.exists():
        raise SystemExit("no pod: run `up` first")
    return json.loads(STATE.read_text(encoding="utf-8"))


def shell():
    s = state()
    return rp.Shell(s["host"], s["port"])


def set_deadline(sh):
    """Writes now + what the balance covers (capped by the `up` hours) into /workspace/deadline."""
    s, left = state(), rp.balance() - RESERVE_USD
    seconds = min(left / s["rate"] * 3600, s["until"] - time.time())
    if seconds < 600:
        raise SystemExit(f"balance ${left + RESERVE_USD:.2f} or --hours covers under 10 minutes; `down` or top up")
    sh.run(f"echo {int(time.time() + seconds)} > {W}/deadline")
    print(f"balance ${left + RESERVE_USD:.2f}, pod ${s['rate']:.2f}/h: auto-stop in {seconds / 3600:.1f} h")


def sync(sh, raws=()):
    """Uploads code always, and the tokenizer, shards and init_from checkpoints that are missing or differ."""
    code = {f"lab/{p.relative_to(REPO / 'lab').as_posix()}": p for p in (REPO / "lab").rglob("*")
            if p.suffix in (".py", ".yaml") and "__pycache__" not in p.parts}
    sh.put(code | {"pipeline/paths.py": REPO / "pipeline" / "paths.py"}, f"{W}/code")
    have = sh.sizes(DATA)
    local = {f"tokenizer/{p.name}": p for p in corpus.TOKENIZER_PATH.parent.iterdir() if p.is_file()}
    local |= {f"tokens/lab-vi-32k/{p.name}": p for p in corpus.TOKENS_DIR.iterdir() if p.is_file()}
    for r in raws:
        base, ckpt = r["train"].get("init_from"), r["train"].get("init_ckpt", "ckpt.pt")
        if base and f"runs/{base}/{ckpt}" not in have:  # a base trained on the pod is newer than any local copy
            local |= {f"runs/{base}/{n}": corpus.RUNS_DIR / base / n for n in ("config.yaml", ckpt)}
    todo = {arc: p for arc, p in local.items() if have.get(arc) != p.stat().st_size}
    if todo:
        print(f"uploading {len(todo)} files ({sum(p.stat().st_size for p in todo.values()) / 1e9:.2f} GB)", flush=True)
        sh.put(todo, DATA)


def cloud_config(path, micro_batch, name=None):
    raw = yaml.safe_load(pathlib.Path(path).read_text(encoding="utf-8"))
    raw["train"]["ckpt_minutes"] = 10  # an interruption or the deadline loses at most 10 minutes
    if micro_batch:
        raw["train"]["micro_batch"] = micro_batch
    if name:
        raw["train"]["name"] = name
    return raw


def put_configs(sh, raws):
    tmp = corpus.LAB_DATA / "runpod" / "jobs"
    tmp.mkdir(parents=True, exist_ok=True)
    files = {}
    for raw in raws:
        files[f"{raw['train']['name']}.yaml"] = tmp / f"{raw['train']['name']}.yaml"
        files[f"{raw['train']['name']}.yaml"].write_text(yaml.safe_dump(raw, allow_unicode=True, sort_keys=False),
                                                         encoding="utf-8")
    sh.put(files, f"{W}/jobs")


def train_cmd(name, max_steps=None):
    return (f"source {W}/env.sh && cd {W}/code && {W}/venv/bin/python -m lab.train {W}/jobs/{name}.yaml"
            + (f" --max-steps {max_steps}" if max_steps else ""))


def create(hours, cloud, only):
    """Creates the pod on the first GPU in GPUS (or only `only`) that Runpod places now in `cloud`; exits
    NO_CAPACITY when none is."""
    rp.ensure_ssh_key()
    prices, refusals = rp.gpu_prices(cloud), []
    for name in [only] if only else GPUS:
        gpu_id, price = prices[name]
        if rp.balance() - RESERVE_USD < price:
            raise SystemExit(f"balance below one hour of {name} (${price}/h)")
        pod, refusal = rp.create_pod({
            "name": "llm-lab", "cloud": cloud, "image": IMAGE, "ports": ["22/tcp"], "startSsh": True,
            "disk": DISK_GB, "mounts": {"persistent": {"size": VOLUME_GB, "path": W}},
            "gpu": {"id": gpu_id, "count": 1, "minCudaVersion": "13.0"}})  # torch cu130 wheel
        if pod:
            STATE.parent.mkdir(parents=True, exist_ok=True)
            STATE.write_text(json.dumps({"id": pod["id"], "gpu": name, "rate": pod["cost"],
                                         "until": time.time() + hours * 3600, "balance_at_up": rp.balance()}),
                             encoding="utf-8")
            print(f"created pod {pod['id']} ({cloud} {name}, ${pod['cost']}/h); waiting for it to boot", flush=True)
            return
        refusals.append(f"{name}: {refusal}")
    print("no capacity now; " + " | ".join(refusals), flush=True)
    sys.exit(NO_CAPACITY)


def up(hours, cloud, only):
    if STATE.exists():
        print(f"pod {state()['id']} exists; re-syncing")
    else:
        create(hours, cloud, only)
    s = state()
    if rp.call("GET", f"/pods/{s['id']}")["status"] == "EXITED":  # stopped by the watchdog or from the console
        rp.call("POST", f"/pods/{s['id']}/action", json={"action": "start"})
    s["host"], s["port"] = rp.wait_running(s["id"])
    STATE.write_text(json.dumps(s), encoding="utf-8")
    sh = shell()
    sh.wait_ssh()
    sh.run(f"mkdir -p {W} && date -d '+20 min' +%s > {W}/deadline")  # the watchdog needs a deadline to start
    sh.run(SETUP.replace("@PEAK@", f"{GPUS[state()['gpu']]:.0f}"))
    set_deadline(sh)
    sync(sh)
    sh.run(f"source {W}/env.sh && {W}/venv/bin/python -c 'import torch, fla; print(torch.__version__, "
           f"torch.cuda.get_device_name(), torch.version.cuda)'")


def queue_running(sh):
    if sh.run("pgrep -f '[q]ueue.sh' >/dev/null", check=False) == 0:  # [q]: skip the probing shell
        raise SystemExit("a queue is still running; see `status`")


def bench(config, micro_batch):
    sh = shell()
    queue_running(sh)
    set_deadline(sh)
    sync(sh)
    name = f"bench-{pathlib.Path(config).stem}-mb{micro_batch or 'cfg'}"
    sh.run(f"rm -rf {DATA}/runs/{name}")
    put_configs(sh, [cloud_config(config, micro_batch, name=name)])
    code = sh.run(train_cmd(name, max_steps=30), check=False)
    log = sh.run(f"cat {DATA}/runs/{name}/log.jsonl 2>/dev/null || true", capture=True)
    records = [json.loads(line) for line in log.splitlines()]
    sh.run(f"rm -rf {DATA}/runs/{name}")
    tok_s = sorted(r["tok_s"] for r in records[5:])  # the first steps include compilation
    print(f"exit {code}; median {tok_s[len(tok_s) // 2] if tok_s else '?'} tok/s, "
          f"mfu {records[-1].get('mfu') if records else '?'}")


def train(configs, micro_batch):
    sh = shell()
    queue_running(sh)
    raws = [cloud_config(c, micro_batch) for c in configs]
    set_deadline(sh)
    sync(sh, raws)
    put_configs(sh, raws)
    # On any exit (done, failed, killed by the watchdog) the pod gets IDLE_GRACE_S to be fetched from.
    idle = (f'trap \'d=$(( $(date +%s) + {IDLE_GRACE_S} )); '
            f'[ $d -lt $(cat {W}/deadline) ] && echo $d > {W}/deadline\' EXIT')
    lines = [f'echo "$(date -Is) start {r["train"]["name"]}"; {train_cmd(r["train"]["name"])} || '
             f'{{ echo "$(date -Is) {r["train"]["name"]} failed: queue stopped"; exit 1; }}' for r in raws]
    done = 'echo "$(date -Is) queue done"'
    sh.put({"queue.sh": _script(corpus.LAB_DATA / "runpod" / "queue.sh", "\n".join([idle] + lines + [done]))}, W)
    sh.run(f"cd {W} && (setsid nohup bash queue.sh >> queue.log 2>&1 &)")
    print(f"queued {', '.join(r['train']['name'] for r in raws)}; watch with `status`")


def _script(path, text):
    path.write_bytes(text.encode("utf-8") + b"\n")
    return path


def status():
    s, sh = state(), shell()
    print(sh.run(f"tail -n 8 {W}/queue.log 2>/dev/null; pgrep -f '[q]ueue.sh' >/dev/null && echo '[queue running]'; "
                 f"echo auto-stop in $(( ($(cat {W}/deadline) - $(date +%s)) / 60 )) min; "
                 f"for d in {DATA}/runs/*/; do echo \"$(basename $d): $(tail -n 1 $d/log.jsonl 2>/dev/null | "
                 f"cut -c1-160)\"; done", capture=True))
    bal = rp.balance()
    print(f"balance ${bal:.2f} (spent ${s['balance_at_up'] - bal:.2f} since `up`), pod ${s['rate']:.2f}/h")


def fetch(run, no_stable):
    sh = shell()
    names = [n for n in sh.sizes(f"{DATA}/runs/{run}") if not (no_stable and n == "ckpt-stable.pt")]
    if not names:
        raise SystemExit(f"no run {run} on the pod")
    sh.get(f"{DATA}/runs/{run}", names, corpus.RUNS_DIR / run)
    print(f"fetched {run} -> {corpus.RUNS_DIR / run}")


def down(force):
    s = state()
    if not force:  # --force also covers pods that never became reachable over SSH
        remote = shell().sizes(f"{DATA}/runs")
        missing = sorted({p.split("/")[0] for p, size in remote.items() if p.endswith("ckpt.pt")
                          and not ((corpus.RUNS_DIR / p).exists() and (corpus.RUNS_DIR / p).stat().st_size == size)})
        if missing:
            raise SystemExit(f"not fetched yet: {', '.join(missing)}; `fetch` them or pass --force")
    rp.call("DELETE", f"/pods/{s['id']}")
    STATE.unlink()
    print(f"terminated pod {s['id']}; balance ${rp.balance():.2f}")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("up")
    p.add_argument("--hours", type=float, default=12, help="hard cap on the pod's lifetime")
    p.add_argument("--cloud", choices=CLOUDS, default="COMMUNITY")
    p.add_argument("--gpu", choices=list(GPUS), help="try only this GPU (default: each in GPUS order)")
    for name in ("bench", "train"):
        p = sub.add_parser(name)
        p.add_argument("configs", nargs="+" if name == "train" else 1)
        p.add_argument("--micro-batch", type=int)
    sub.add_parser("status")
    p = sub.add_parser("fetch")
    p.add_argument("run")
    p.add_argument("--no-stable", action="store_true", help="skip ckpt-stable.pt")
    sub.add_parser("down").add_argument("--force", action="store_true", help="terminate with runs not fetched")
    args = parser.parse_args()
    if args.cmd == "up":
        up(args.hours, args.cloud, args.gpu)
    elif args.cmd == "bench":
        bench(args.configs[0], args.micro_batch)
    elif args.cmd == "train":
        train(args.configs, args.micro_batch)
    elif args.cmd == "status":
        status()
    elif args.cmd == "fetch":
        fetch(args.run, args.no_stable)
    else:
        down(args.force)


if __name__ == "__main__":
    main()
