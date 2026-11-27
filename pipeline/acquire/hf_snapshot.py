"""
Download open datasets from the Hugging Face Hub at a pinned revision.

Declarations live in manifests/datasets/<name>.yaml (in git). A download writes
manifests/datasets/<name>.lock.json: every file with its size and the Hub's LFS
sha256, so a later `verify` (or a re-download elsewhere) can prove byte identity.

    uv run python -m pipeline.acquire.hf_snapshot plan [--wave 1]
    uv run python -m pipeline.acquire.hf_snapshot download [names...] [--wave 1]
    uv run python -m pipeline.acquire.hf_snapshot verify <name>

Pretraining data goes to RAW_DIR/open_datasets/<name>; eval sets to DATA_ROOT/eval/<name>
(never mixed into training, see docs/data-contract.md). Gated repos need HF_TOKEN
and the dataset terms accepted on the Hub; they are skipped with a message otherwise.
"""
import argparse
import fnmatch
import hashlib
import json
import os
import pathlib
import shutil
import sys
from datetime import datetime, timezone

import yaml

os.environ.setdefault("HF_XET_HIGH_PERFORMANCE", "1")
from huggingface_hub import HfApi, snapshot_download  # noqa: E402  (env must be set first)
from huggingface_hub.errors import GatedRepoError  # noqa: E402

from pipeline.paths import DATA_ROOT, RAW_DIR, ROOT  # noqa: E402

MANIFEST_DIR = ROOT / "manifests" / "datasets"
FREE_SPACE_MARGIN = 50 * 2**30


def load_declarations():
    decls = {}
    for path in sorted(MANIFEST_DIR.glob("*.yaml")):
        decl = yaml.safe_load(path.read_text(encoding="utf-8"))
        decls[decl["name"]] = decl
    return decls


def target_dir(decl):
    base = DATA_ROOT / "eval" if decl["purpose"] == "eval" else RAW_DIR / "open_datasets"
    return base / decl["name"]


def remote_files(decl):
    """Files selected by allow_patterns at the pinned revision: [(path, size, sha256)]."""
    info = HfApi().dataset_info(decl["repo"], revision=decl["revision"], files_metadata=True)
    patterns = decl.get("allow_patterns") or ["*"]
    return [(s.rfilename, s.size or 0, s.lfs.sha256 if s.lfs else None) for s in info.siblings
            if any(fnmatch.fnmatch(s.rfilename, p) for p in patterns)]


def plan(decls):
    total = 0
    for decl in decls:
        size = sum(size for _, size, _ in remote_files(decl))
        total += size
        print(f"{decl['name']:22} {decl['purpose']:8} {size / 2**30:8.1f} GB  gated={decl.get('gated', False)}")
    free = shutil.disk_usage(DATA_ROOT.anchor or DATA_ROOT).free
    print(f"{'TOTAL':22} {'':8} {total / 2**30:8.1f} GB; free on {DATA_ROOT.anchor}: {free / 2**30:.0f} GB")
    return total


def download(decl):
    files = remote_files(decl)
    size = sum(s for _, s, _ in files)
    out = target_dir(decl)
    out.mkdir(parents=True, exist_ok=True)
    if shutil.disk_usage(out).free - size < FREE_SPACE_MARGIN:
        raise RuntimeError(f"{decl['name']}: needs {size / 2**30:.0f} GB, not enough free space on {out.anchor}")
    try:
        snapshot_download(decl["repo"], repo_type="dataset", revision=decl["revision"],
                          allow_patterns=decl.get("allow_patterns"), local_dir=out, max_workers=8)
    except GatedRepoError:
        print(f"{decl['name']}: gated. Accept the terms at https://huggingface.co/datasets/{decl['repo']} "
              f"and set HF_TOKEN, then re-run.")
        return
    lock = {"name": decl["name"], "repo": decl["repo"], "revision": decl["revision"],
            "downloaded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "local_dir": out.relative_to(DATA_ROOT).as_posix(), "bytes": size,
            "files": [{"path": p, "size": s, "sha256": h} for p, s, h in files]}
    (MANIFEST_DIR / f"{decl['name']}.lock.json").write_text(json.dumps(lock, indent=1), encoding="utf-8")
    print(f"{decl['name']}: {len(files)} files, {size / 2**30:.1f} GB -> {out}")


def verify(decl):
    lock = json.loads((MANIFEST_DIR / f"{decl['name']}.lock.json").read_text(encoding="utf-8"))
    base, bad = DATA_ROOT / lock["local_dir"], []
    for f in lock["files"]:
        path = pathlib.Path(base, f["path"])
        if not path.exists() or path.stat().st_size != f["size"]:
            bad.append(f"{f['path']}: missing or wrong size")
        elif f["sha256"]:
            digest = hashlib.sha256()
            with open(path, "rb") as fh:
                while chunk := fh.read(16 * 2**20):
                    digest.update(chunk)
            if digest.hexdigest() != f["sha256"]:
                bad.append(f"{f['path']}: sha256 mismatch")
    print(f"{decl['name']}: {len(lock['files']) - len(bad)}/{len(lock['files'])} files ok")
    for line in bad:
        print("  ", line)
    return not bad


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["plan", "download", "verify"])
    parser.add_argument("names", nargs="*")
    parser.add_argument("--wave", type=int)
    args = parser.parse_args()
    decls = load_declarations()
    unknown = set(args.names) - set(decls)
    if unknown:
        parser.error(f"unknown datasets: {sorted(unknown)}")
    selected = [d for n, d in decls.items()
                if (not args.names or n in args.names) and (args.wave is None or d["wave"] == args.wave)]
    if args.command == "plan":
        plan(selected)
    elif args.command == "download":
        for decl in selected:
            download(decl)
    else:
        sys.exit(0 if all(verify(d) for d in selected) else 1)


if __name__ == "__main__":
    main()
